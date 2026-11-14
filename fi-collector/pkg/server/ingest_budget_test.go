package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// Tests for the ingest budget (ingest_pool.go, ingest_limits.go): every
// OTLP/HTTP request, plain or gzip, leases its body from the wire pool before
// reading it and its price from the work pool before decoding it; leases are
// bounded, granted in arrival order within a bounded wait, and given back
// when the handler returns, or, for the rows, when the drain that stores them
// ends.

// holdPool leases n bytes, standing in for in-flight requests; the returned
// func releases them and may be called more than once.
func holdPool(t *testing.T, p *memPool, n int64) func() {
	t.Helper()
	l, err := p.acquire(context.Background(), n)
	if err != nil {
		t.Fatalf("hold %d of %d %s bytes: %v", n, p.size, p.name, err)
	}
	return l.release
}

func postGzipTo(s *Server, body []byte) *httptest.ResponseRecorder {
	return postBody(s, "application/x-protobuf", true, nil, body)
}

// zeroGzipMembers is n concatenated gzip members of 1 MiB of zeros each: n MiB
// once inflated, about n KiB on the wire.
func zeroGzipMembers(t *testing.T, n int) []byte {
	t.Helper()
	member := gzipBytes(t, make([]byte, 1<<20))
	return bytes.Repeat(member, n)
}

// tracesOfSize is a valid request with one span whose attribute holds n bytes,
// so its protobuf is a little over n bytes and gzips to a few KiB.
func tracesOfSize(t *testing.T, name string, n int) []byte {
	t.Helper()
	traces := makeTraces(name, "21212121-2121-4121-8121-212121212121")
	traces.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).
		Attributes().PutStr("raw_log", strings.Repeat("x", n))
	return protoBody(t, traces)
}

// budgetServer accepts and parks rows, never flushing on its own, with an
// ingest budget of mib MiB.
func budgetServer(mib int) *Server {
	return New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour, IngestBudgetMiB: mib}, nil, nil, nil, nil)
}

// flushingServer has an ingest budget of mib MiB and a running flusher that
// stores rows in a stub ClickHouse, as Run would, without listeners. It
// flushes only when kicked or after age.
func flushingServer(t *testing.T, mib int, age time.Duration) (*Server, *chBodies) {
	t.Helper()
	return flushingServerConfig(t, Config{BatchMaxRows: 1 << 30, BatchMaxAge: age, IngestBudgetMiB: mib})
}

func flushingServerConfig(t *testing.T, cfg Config) (*Server, *chBodies) {
	t.Helper()
	ch, w := newCHBodies(t)
	s := New(cfg, w, nil, nil, nil)
	s.wg.Add(1)
	go s.flushLoop()
	t.Cleanup(func() {
		close(s.stopCh)
		s.wg.Wait()
	})
	return s, ch
}

// While the wire pool is full, a request waits about the lease wait, then is
// a 503 with Retry-After, plain or gzip; the same request succeeds once the
// pool is back. (Before the ingest budget, only gzip bodies were budgeted, and only once
// decompressed: a plain body was read and decoded however full memory was.)
func TestOTLPHTTPWireBusyIs503(t *testing.T) {
	raw := tracesOfSize(t, "budget-span", 2<<20)
	for name, gzipped := range map[string]bool{"plain": false, "gzip": true} {
		t.Run(name, func(t *testing.T) {
			body := raw
			if gzipped {
				body = gzipBytes(t, raw)
			}
			s := acceptingServer()
			s.wire.wait = 200 * time.Millisecond
			hold := holdPool(t, s.wire, s.wire.size-1<<20) // all but 1 MiB

			start := time.Now()
			rec := postBody(s, "application/x-protobuf", gzipped, nil, body)
			waited := time.Since(start)
			if rec.Code != http.StatusServiceUnavailable {
				t.Fatalf("status %d, want 503 while the wire pool is full (%s)", rec.Code, rec.Body.String())
			}
			if rec.Header().Get("Retry-After") == "" {
				t.Error("503 must carry Retry-After so exporters back off and retry")
			}
			if waited < s.wire.wait || waited > s.wire.wait+time.Second {
				t.Errorf("answered 503 after %v; want it to wait about %v first", waited, s.wire.wait)
			}

			hold()
			if rec := postBody(s, "application/x-protobuf", gzipped, nil, body); rec.Code != http.StatusOK {
				t.Fatalf("status %d after the pool freed up, want 200 (%s)", rec.Code, rec.Body.String())
			}
			if n := s.wire.inUse.Load(); n != 0 {
				t.Errorf("%d wire bytes still leased after every request returned", n)
			}
		})
	}
}

// The wire lease is taken before any of the body is read: even a corrupt
// gzip body waits for it and gets the pool's 503 before it is inspected;
// once the pool is free, that same body is a 400.
func TestOTLPHTTPWireLeaseComesBeforeTheBody(t *testing.T) {
	s := acceptingServer()
	s.wire.wait = 50 * time.Millisecond
	bad := []byte("not gzip")
	hold := holdPool(t, s.wire, s.wire.size)
	start := time.Now()
	if rec := postGzipTo(s, bad); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("with the pool held, got %d, want 503 before gzip is inspected (%s)", rec.Code, rec.Body.String())
	}
	if waited := time.Since(start); waited < s.wire.wait {
		t.Errorf("waited %v, want to wait for the pool before inspecting the body", waited)
	}
	hold()
	if rec := postGzipTo(s, bad); rec.Code != http.StatusBadRequest {
		t.Fatalf("with the pool free, got %d, want 400 for corrupt gzip (%s)", rec.Code, rec.Body.String())
	}
}

// A request that finds the pool full waits for it instead of failing at
// once, and gets 200 when the pool frees up within the wait.
func TestOTLPHTTPWaitsForTheWirePool(t *testing.T) {
	body := gzipBytes(t, tracesOfSize(t, "wait-span", 2<<20))
	s := acceptingServer()
	hold := holdPool(t, s.wire, s.wire.size)
	time.AfterFunc(200*time.Millisecond, hold)
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("status %d, want 200 once the pool frees up within the %v wait (%s)", rec.Code, s.wire.wait, rec.Body.String())
	}
}

// Waiters are served in arrival order, so a large body waiting for its share
// is not starved by small bodies that arrive after it.
func TestOTLPHTTPLargeBodyNotStarvedBySmallOnes(t *testing.T) {
	s, _ := flushingServer(t, 96, time.Hour) // wire pool 32 MiB
	s.wire.wait = 3 * time.Second
	large := tracesOfSize(t, "large-span", 12<<20) // plain: leases its 12 MiB
	small := gzipBytes(t, tracesOfSize(t, "small-span", 1<<20))

	hold := holdPool(t, s.wire, 24<<20) // other requests hold three quarters of the pool
	largeCode := make(chan int, 1)
	go func() { largeCode <- postBody(s, "application/x-protobuf", false, nil, large).Code }()
	// Wait until the large request is queued: a queued waiter makes
	// TryAcquire fail even though the pool has room.
	for deadline := time.Now().Add(5 * time.Second); s.wire.sem.TryAcquire(1); time.Sleep(time.Millisecond) {
		s.wire.sem.Release(1)
		select {
		case code := <-largeCode:
			hold()
			t.Fatalf("large request answered %d without waiting for the pool", code)
		default:
		}
		if time.Now().After(deadline) {
			hold()
			t.Fatal("large request never queued for the pool")
		}
	}

	stop := make(chan struct{})
	var wg sync.WaitGroup
	var smallOK atomic.Int32
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				if postGzipTo(s, small).Code == http.StatusOK {
					smallOK.Add(1)
				}
			}
		}()
	}
	time.Sleep(300 * time.Millisecond)
	hold()
	code := <-largeCode
	close(stop)
	wg.Wait()

	if code != http.StatusOK {
		t.Fatalf("large request: status %d, want 200; it must not be starved by later small ones", code)
	}
	if smallOK.Load() == 0 {
		t.Error("no small request succeeded; they should run once the large one has its share")
	}
	if p := s.wire.peak.Load(); p > s.wire.size {
		t.Errorf("peak leased %d bytes, over the %d byte pool", p, s.wire.size)
	}
}

// leaseProbe is a pricer: conversion calls it for every span, so it sees
// what is leased while the request is being converted.
type leaseProbe struct {
	s          *Server
	wire, work atomic.Int64
}

func (p *leaseProbe) TokenCost(context.Context, string, string, int32, int32) (float64, bool) {
	p.wire.Store(p.s.wire.inUse.Load())
	p.work.Store(p.s.work.inUse.Load())
	return 0, false
}

func (p *leaseProbe) OpenRouterTokenCost(ctx context.Context, orgID, model string, prompt, completion int32) (float64, bool) {
	return p.TokenCost(ctx, orgID, model, prompt, completion)
}

// While a request is converted it holds its body (wire) and its price (work).
// When the handler returns the body and the decoded request are given back,
// and the rows' share stays with the pending batch until the drain that
// stores the rows ends. (Before the ingest budget, nothing was held for decoding or rows,
// and the gzip lease ended when the handler returned, while the rows stayed
// in memory until they were flushed.)
func TestOTLPHTTPLeasesFollowTheBodyAndTheRows(t *testing.T) {
	raw := tracesOfSize(t, "lease-span", 3<<20)
	for name, gzipped := range map[string]bool{"plain": false, "gzip": true} {
		t.Run(name, func(t *testing.T) {
			ch, w := newCHBodies(t)
			probe := &leaseProbe{}
			s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, w, nil, nil, nil, WithPricer(probe))
			probe.s = s
			body := raw
			if gzipped {
				body = gzipBytes(t, raw)
			}
			shape, err := scanOTLPProto(raw)
			if err != nil {
				t.Fatal(err)
			}
			price := shape.price()

			if rec := postBody(s, "application/x-protobuf", gzipped, nil, body); rec.Code != http.StatusOK {
				t.Fatalf("status %d (%s)", rec.Code, rec.Body.String())
			}
			if got := probe.wire.Load(); got != int64(len(raw)) {
				t.Errorf("wire lease while converting: %d bytes, want the decoded body's %d", got, len(raw))
			}
			if got := probe.work.Load(); got != price.total() {
				t.Errorf("work lease while converting: %d bytes, want the price %d", got, price.total())
			}
			if u := s.wire.inUse.Load(); u != 0 {
				t.Errorf("%d wire bytes leased after the handler returned, want 0", u)
			}
			if u := s.work.inUse.Load(); u != price.rows {
				t.Errorf("%d work bytes leased with the rows pending, want their share %d", u, price.rows)
			}
			s.drainNow(context.Background())
			if n, _ := ch.lines("spans"); n == 0 {
				t.Fatal("the drain stored nothing")
			}
			if u := s.work.inUse.Load(); u != 0 {
				t.Errorf("%d work bytes leased after the drain, want 0", u)
			}
		})
	}
}

// A refused request gives every lease back, whatever refused it.
func TestOTLPHTTPRefusalsReleaseTheirLeases(t *testing.T) {
	ch, w := newCHBodies(t)
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, w, nil, nil, nil)
	_ = ch
	ok := protoBody(t, makeTraces("ok", "41414141-4141-4141-8141-414141414141"))
	cases := map[string]struct {
		body    []byte
		ct      string
		gzipped bool
		header  map[string]string
		want    int
	}{
		"corrupt protobuf":     {[]byte{0x0a, 0x05, 0x00}, "application/x-protobuf", false, nil, 400},
		"corrupt JSON":         {[]byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":7}]}]}]}`), "application/json", false, nil, 400},
		"bad project header":   {ok, "application/x-protobuf", false, map[string]string{"X-Project-Name": "a,b"}, 400},
		"truncated gzip":       {gzipBytes(t, ok)[:20], "application/x-protobuf", true, nil, 400},
		"over the body cap":    {make([]byte, defaultMaxBodyBytes+1), "application/x-protobuf", false, nil, 413},
		"accepted, then drain": {ok, "application/x-protobuf", false, nil, 200},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if rec := postBody(s, tc.ct, tc.gzipped, tc.header, tc.body); rec.Code != tc.want {
				t.Fatalf("status %d, want %d (%s)", rec.Code, tc.want, rec.Body.String())
			}
			s.drainNow(context.Background())
			if w, k := s.wire.inUse.Load(), s.work.inUse.Load(); w != 0 || k != 0 {
				t.Errorf("leased after the request: wire %d, work %d; want 0", w, k)
			}
		})
	}
}

// Concurrent gzip bombs share the wire pool: none of them can hold more than
// it, each is turned away once it would pass the cap, and every lease is
// returned.
func TestOTLPHTTPConcurrentGzipBombsStayWithinTheWirePool(t *testing.T) {
	s := budgetServer(96)
	bomb := zeroGzipMembers(t, 64) // 64 MiB inflated, ~64 KiB on the wire

	const n = 32
	codes := make([]int, n)
	var wg sync.WaitGroup
	for i := range codes {
		wg.Add(1)
		go func() {
			defer wg.Done()
			codes[i] = postGzipTo(s, bomb).Code
		}()
	}
	wg.Wait()

	for i, c := range codes {
		if c != http.StatusRequestEntityTooLarge && c != http.StatusServiceUnavailable {
			t.Errorf("request %d: status %d, want 413 or bounded-wait 503", i, c)
		}
	}
	if p := s.wire.peak.Load(); p > s.wire.size {
		t.Errorf("peak leased %d bytes, over the %d byte pool", p, s.wire.size)
	}
	if u := s.wire.inUse.Load(); u != 0 {
		t.Errorf("%d wire bytes still leased after every request returned", u)
	}
}

// A body is leased before it is read; an upload that stalls gives its lease
// back when the read timeout answers it with a busy refusal (503 and
// Retry-After), which exporters retry.
func TestOTLPHTTPStalledUploadsTimeOut(t *testing.T) {
	s := budgetServer(96)
	s.readTimeout = 500 * time.Millisecond
	s.wire.wait = 3 * time.Second
	srv := httptest.NewServer(http.HandlerFunc(s.handleHTTPTraces))
	t.Cleanup(srv.Close)

	type result struct {
		code int
		err  error
	}
	// The rest of the pool is held, so the stalled upload's lease is all
	// that is left.
	want := s.limits.maxBody + readChunk // a body of unknown length: the cap, read in chunks
	hold := holdPool(t, s.wire, s.wire.size-want)
	defer hold()
	stalled := make(chan result, 1)
	pr, pw := io.Pipe()
	t.Cleanup(func() { _ = pw.Close() })
	go func() { _, _ = pw.Write(zeroGzipMembers(t, 2)) }() // then stall, without closing
	go func() {
		req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/traces", pr) // no Content-Length
		req.Header.Set("Content-Type", "application/x-protobuf")
		req.Header.Set("Content-Encoding", "gzip")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			stalled <- result{err: err}
			return
		}
		resp.Body.Close()
		stalled <- result{code: resp.StatusCode}
	}()
	for deadline := time.Now().Add(2 * time.Second); s.wire.inUse.Load() < s.wire.size && time.Now().Before(deadline); {
		time.Sleep(5 * time.Millisecond)
	}
	if held := s.wire.inUse.Load(); held != s.wire.size {
		t.Fatalf("pool holds %d bytes with the stalled upload, want all %d", held, s.wire.size)
	}

	// A request now waits for the stalled one to time out, then succeeds.
	body := gzipBytes(t, tracesOfSize(t, "during-stall", 1<<20))
	start := time.Now()
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("request while an upload stalls: status %d, want 200 after it times out (%s)", rec.Code, rec.Body.String())
	}
	if waited := time.Since(start); waited > 2*time.Second {
		t.Errorf("waited %v for the stalled upload, want about the %v read timeout", waited, s.readTimeout)
	}
	select {
	case r := <-stalled:
		if r.err == nil && r.code != http.StatusServiceUnavailable {
			t.Errorf("stalled upload: status %d, want 503", r.code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the stalled upload was never answered")
	}
	hold()
	if u := s.wire.inUse.Load(); u != 0 {
		t.Errorf("%d wire bytes still leased after the stalled upload ended", u)
	}
}

// emptySpans is a request of n spans with no fields set: 2 bytes each in
// protobuf, a few KiB each decoded and converted.
func emptySpans(t *testing.T, n int) []byte {
	t.Helper()
	return pbField(1, pbField(2, bytes.Repeat(pbField(2, nil), n)))
}

// A body whose decoded price is more than the server can give one request is
// refused permanently (413) before it is decoded, plain or gzip, with the
// setting to raise in the message; a body that fits is decoded. (Before
// the ingest budget, a plain body was decoded and converted whatever it
// cost.)
func TestOTLPHTTPOverPricedBodyIs413BeforeDecoding(t *testing.T) {
	s := budgetServer(96) // work pool 64 MiB
	body := emptySpans(t, 60000)
	for name, gzipped := range map[string]bool{"plain": false, "gzip": true} {
		t.Run(name, func(t *testing.T) {
			b := body
			if gzipped {
				b = gzipBytes(t, body)
			}
			rec := postBody(s, "application/x-protobuf", gzipped, nil, b)
			if rec.Code != http.StatusRequestEntityTooLarge || !strings.Contains(rec.Body.String(), "FI_INGEST_BUDGET_MIB") {
				t.Fatalf("status %d %q, want 413 naming FI_INGEST_BUDGET_MIB", rec.Code, rec.Body.String())
			}
			if p := s.work.peak.Load(); p != 0 {
				t.Errorf("leased %d work bytes for a refused body, want none", p)
			}
		})
	}
	if rec := postBody(s, "application/x-protobuf", false, nil, emptySpans(t, 2000)); rec.Code != http.StatusOK {
		t.Errorf("2,000 empty spans: status %d, want 200 (%s)", rec.Code, rec.Body.String())
	}
}

// Requests that together need more than the work pool wait for it: none is
// refused permanently, and the pool is never exceeded. Waiting requests start
// a drain, which frees the rows' share (rows otherwise wait for the 5 s
// flush).
func TestOTLPHTTPConcurrentRequestsShareTheWorkPool(t *testing.T) {
	// This is an order/bounds fixture, not the production timeout contract.
	// Set the constructor value: work.wait alone leaves limits.maxWait (and
	// hence the shared wire/work leaseBudget) at the production two seconds.
	s, ch := flushingServerConfig(t, Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour,
		IngestBudgetMiB: 96, IngestMaxWait: time.Minute})
	s.work.wait = time.Minute // this fixture also permits slow no-progress decode under -race
	if s.limits.maxWait != time.Minute || s.work.maxWait != time.Minute {
		t.Fatal("fixture must extend the actual shared lease wait, not only work.wait")
	}

	body := emptySpans(t, 3000) // about 21 MiB each: three fit at once
	const n = 8
	codes := make([]int, n)
	var wg sync.WaitGroup
	for i := range codes {
		wg.Add(1)
		go func() {
			defer wg.Done()
			codes[i] = postBody(s, "application/x-protobuf", false, nil, body).Code
		}()
	}
	wg.Wait()
	for i, c := range codes {
		if c != http.StatusOK {
			t.Errorf("request %d: status %d, want 200 after waiting its turn", i, c)
		}
	}
	if p := s.work.peak.Load(); p > s.work.size {
		t.Errorf("peak leased %d work bytes, over the %d byte pool", p, s.work.size)
	}
	if s.work.waits.Load() == 0 {
		t.Error("no request waited: the test does not exercise the pool")
	}
	s.drainNow(context.Background())
	if n, _ := ch.lines("spans"); n == 0 {
		t.Error("nothing stored")
	}
}

// meteringProbe refuses every request and records how much of the body was
// leased when it did.
type meteringProbe struct {
	s    *Server
	seen atomic.Int64
}

func (m *meteringProbe) CheckUsage(context.Context, string, string, int64) auth.CheckResult {
	m.seen.Store(m.s.wire.inUse.Load() + 1)
	return auth.CheckResult{Allowed: false, Reason: "probe"}
}

// The quota is checked before any of the body is read.
func TestOTLPHTTPQuotaIsCheckedBeforeTheBodyIsRead(t *testing.T) {
	probe := &meteringProbe{}
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, nil, nil, nil, probe)
	probe.s = s
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(tracesOfSize(t, "quota", 1<<20)))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req = req.WithContext(auth.WithResolvedContext(req.Context(), &auth.ResolveResult{OrgID: "org"}, "key"))
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status %d, want the probe's 429 (%s)", rec.Code, rec.Body.String())
	}
	if got := probe.seen.Load() - 1; got != 0 {
		t.Errorf("%d wire bytes leased when the quota was checked, want 0: it runs before the body is read", got)
	}
	if p := s.wire.peak.Load(); p != 0 {
		t.Errorf("leased %d wire bytes for a request over quota, want none", p)
	}
}
