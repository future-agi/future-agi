package server

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"runtime/debug"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// Tests for the process-wide budget on decompressed gzip bytes (inflateBudget).
// The per-request 16 MiB cap bounds one request; these check that many
// concurrent gzip requests cannot add up past the budget, that stalled uploads
// hold none of it, that a request waits a bounded time for its share in
// arrival order, and that the lease is exactly the decompressed size, held
// until the handler returns.

// holdBudget leases n bytes, standing in for in-flight requests; the returned
// func releases them and may be called more than once.
func holdBudget(t *testing.T, b *inflateBudget, n int64) func() {
	t.Helper()
	lease, err := b.reserve(context.Background(), n)
	if err != nil {
		t.Fatalf("hold %d of %d budget bytes: %v", n, b.size, err)
	}
	return lease.release
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

// A gzip request that cannot get its share within the wait is a 503 with
// Retry-After, after waiting about that long; uncompressed requests are not
// budgeted; and the same gzip request succeeds once the budget is back.
func TestOTLPHTTPGzipOverInflateBudgetIs503(t *testing.T) {
	raw := tracesOfSize(t, "budget-span", 2<<20)
	body := gzipBytes(t, raw) // ~2 MiB inflated, a few KiB on the wire

	s := acceptingServer()
	s.inflate.wait = 200 * time.Millisecond
	hold := holdBudget(t, s.inflate, s.inflate.size-1<<20) // in-flight requests holding all but 1 MiB

	start := time.Now()
	rec := postGzipTo(s, body)
	waited := time.Since(start)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status %d, want 503 while the budget is full (%s)", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Retry-After") == "" {
		t.Error("503 must carry Retry-After so exporters back off and retry")
	}
	if waited < s.inflate.wait || waited > s.inflate.wait+time.Second {
		t.Errorf("answered 503 after %v; want it to wait about %v first", waited, s.inflate.wait)
	}

	if rec := postBody(s, "application/x-protobuf", false, nil, raw); rec.Code != http.StatusOK {
		t.Errorf("uncompressed while the budget is full: status %d, want 200 (%s)", rec.Code, rec.Body.String())
	}

	hold()
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("status %d after the budget freed up, want 200 (%s)", rec.Code, rec.Body.String())
	}
	if n := s.inflate.inUse.Load(); n != 0 {
		t.Errorf("%d budget bytes still leased after every request returned", n)
	}
}

// The count-only inflate pass also consumes the budget. While the budget is
// held, even a corrupt gzip body waits and gets the budget's 503 before the
// decoder can inspect it; once the budget is free, that same body is a 400.
func TestOTLPHTTPGzipBudgetCoversTheFirstInflatePass(t *testing.T) {
	s := acceptingServer()
	s.inflate.wait = 50 * time.Millisecond
	bad := []byte("not gzip")
	hold := holdBudget(t, s.inflate, s.inflate.size)
	start := time.Now()
	if rec := postGzipTo(s, bad); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("with the budget held, got %d, want 503 before gzip is inspected (%s)", rec.Code, rec.Body.String())
	}
	if waited := time.Since(start); waited < s.inflate.wait {
		t.Errorf("waited %v, want to wait for the budget before inspecting the body", waited)
	}
	hold()
	if rec := postGzipTo(s, bad); rec.Code != http.StatusBadRequest {
		t.Fatalf("with the budget free, got %d, want 400 for corrupt gzip (%s)", rec.Code, rec.Body.String())
	}
}

// A request that finds the budget full waits for it instead of failing at
// once, and gets 200 when the budget frees up within the wait.
func TestOTLPHTTPGzipWaitsForBudget(t *testing.T) {
	body := gzipBytes(t, tracesOfSize(t, "wait-span", 2<<20))
	s := acceptingServer()
	hold := holdBudget(t, s.inflate, s.inflate.size)
	time.AfterFunc(200*time.Millisecond, hold)
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("status %d, want 200 once the budget frees up within the %v wait (%s)", rec.Code, s.inflate.wait, rec.Body.String())
	}
}

// Waiters are served in arrival order, so a large body waiting for its share
// is not starved by small bodies that arrive after it. Each gzip request
// leases its decompressed size: about 12 MiB for the large one here.
func TestOTLPHTTPLargeGzipNotStarvedBySmallOnes(t *testing.T) {
	s := acceptingServer()
	s.inflate = newInflateBudget(minGzipInflightBytes) // 16 MiB
	s.inflate.wait = 3 * time.Second
	large := gzipBytes(t, tracesOfSize(t, "large-span", 12<<20))
	small := gzipBytes(t, tracesOfSize(t, "small-span", 1<<20))

	// Other requests hold 8 MiB, so the large request's 12 MiB must wait.
	hold := holdBudget(t, s.inflate, 8<<20)
	largeCode := make(chan int, 1)
	go func() { largeCode <- postGzipTo(s, large).Code }()
	// Wait until the large request is queued: a queued waiter makes
	// TryAcquire fail even though budget is free.
	for deadline := time.Now().Add(5 * time.Second); s.inflate.sem.TryAcquire(1); time.Sleep(time.Millisecond) {
		s.inflate.sem.Release(1)
		select {
		case code := <-largeCode:
			hold()
			t.Fatalf("large request answered %d without waiting for the budget", code)
		default:
		}
		if time.Now().After(deadline) {
			hold()
			t.Fatal("large request never queued for the budget")
		}
	}

	// A stream of small requests arrives after the large waiter.
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
	hold() // the other requests finish
	code := <-largeCode
	close(stop)
	wg.Wait()

	if code != http.StatusOK {
		t.Fatalf("large request: status %d, want 200; it must not be starved by later small ones", code)
	}
	if smallOK.Load() == 0 {
		t.Error("no small request succeeded; they should run once the large one has its share")
	}
	if p := s.inflate.peak.Load(); p > s.inflate.size {
		t.Errorf("peak leased %d bytes, over the %d byte budget", p, s.inflate.size)
	}
}

// meteringProbe records how many budget bytes are leased when the handler
// checks usage, which happens after decoding, then refuses the request.
type meteringProbe struct {
	b    *inflateBudget
	seen atomic.Int64
}

func (m *meteringProbe) CheckUsage(context.Context, string, string, int64) auth.CheckResult {
	m.seen.Store(m.b.inUse.Load())
	return auth.CheckResult{Allowed: false, Reason: "probe"}
}

// The lease is exactly the decompressed size and lasts until the handler
// returns (past decoding), not just while the body is inflated.
func TestOTLPHTTPGzipLeaseIsTheDecompressedSizeUntilTheHandlerReturns(t *testing.T) {
	raw := tracesOfSize(t, "lease-span", 3<<20)
	probe := &meteringProbe{}
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, nil, nil, nil, probe)
	probe.b = s.inflate

	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(gzipBytes(t, raw)))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("Content-Encoding", "gzip")
	req = req.WithContext(auth.WithResolvedContext(req.Context(), &auth.ResolveResult{OrgID: "org"}, "key"))
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status %d, want the probe's 429 (%s)", rec.Code, rec.Body.String())
	}
	if got := probe.seen.Load(); got != int64(len(raw)) {
		t.Errorf("leased %d bytes after decoding, want the decompressed size %d", got, len(raw))
	}
	if u := s.inflate.inUse.Load(); u != 0 {
		t.Errorf("%d budget bytes still leased after the request returned", u)
	}
}

// Concurrent gzip bombs share one budget: none of them can hold more than it,
// each is turned away once it would pass the cap, and every lease is returned.
func TestOTLPHTTPConcurrentGzipBombsStayWithinBudget(t *testing.T) {
	s := directServer()
	s.inflate = newInflateBudget(minGzipInflightBytes)
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
	if p := s.inflate.peak.Load(); p > s.inflate.size {
		t.Errorf("peak leased %d bytes, over the %d byte budget", p, s.inflate.size)
	}
	if u := s.inflate.inUse.Load(); u != 0 {
		t.Errorf("%d budget bytes still leased after every request returned", u)
	}
}

// A gzip upload that stalls part way holds a connection and its compressed
// bytes, as a plain upload does, but none of the inflate budget: the body is
// read off the wire before it leases anything. So uploads that stall forever
// cannot starve other gzip requests, however many there are. (Leasing before
// reading let budget/16 MiB stalled uploads, two at GOMEMLIMIT=256MiB, turn
// every other gzip request away with 503.)
func TestOTLPHTTPStalledGzipUploadsHoldNoBudget(t *testing.T) {
	s := acceptingServer()
	s.inflate = newInflateBudget(32 << 20) // GOMEMLIMIT=256MiB: an eighth
	s.inflate.wait = 200 * time.Millisecond
	prefix := zeroGzipMembers(t, 12) // ~12 KiB that inflates to 12 MiB

	const stalled = 3 // one more than the budget fits at 16 MiB each
	pipes := make([]*io.PipeWriter, stalled)
	codes := make([]chan int, stalled)
	for i := range pipes {
		pr, pw := io.Pipe()
		pipes[i] = pw
		codes[i] = make(chan int, 1)
		req := httptest.NewRequest(http.MethodPost, "/v1/traces", pr)
		req.Header.Set("Content-Type", "application/x-protobuf")
		req.Header.Set("Content-Encoding", "gzip")
		go func(c chan int) {
			rec := httptest.NewRecorder()
			s.handleHTTPTraces(rec, req)
			c <- rec.Code
		}(codes[i])
		// io.Pipe returns from Write once the handler has read all of it.
		wrote := make(chan error, 1)
		go func() { _, err := pw.Write(prefix); wrote <- err }()
		select {
		case err := <-wrote:
			if err != nil {
				t.Fatalf("upload %d: write prefix: %v", i, err)
			}
		case <-time.After(5 * time.Second):
			t.Errorf("upload %d: the handler stopped reading the body", i)
		}
	}
	defer func() {
		for _, pw := range pipes {
			_ = pw.CloseWithError(io.ErrUnexpectedEOF)
		}
	}()

	if held := s.inflate.inUse.Load(); held != 0 {
		t.Errorf("%d stalled uploads hold %d budget bytes, want 0", stalled, held)
	}
	body := gzipBytes(t, tracesOfSize(t, "during-stall", 1<<20))
	if rec := postGzipTo(s, body); rec.Code != http.StatusOK {
		t.Fatalf("gzip request while %d uploads stall: status %d, want 200 (%s)", stalled, rec.Code, rec.Body.String())
	}

	for i, pw := range pipes {
		_ = pw.CloseWithError(io.ErrUnexpectedEOF) // the sender gives up
		select {
		case code := <-codes[i]:
			if code != http.StatusBadRequest {
				t.Errorf("upload %d: status %d after the sender gave up, want 400", i, code)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("upload %d: handler did not return after the sender gave up", i)
		}
	}
	if u := s.inflate.inUse.Load(); u != 0 {
		t.Errorf("%d budget bytes still leased after every request returned", u)
	}
}

func TestGzipInflightBytes(t *testing.T) {
	prev := debug.SetMemoryLimit(-1)
	t.Cleanup(func() { debug.SetMemoryLimit(prev) })

	cases := []struct {
		name     string
		memLimit int64
		mib      int
		want     int64
	}{
		{"unset", 1 << 62, 0, 128 << 20},
		{"configured", 1 << 62, 100, 100 << 20},
		{"configured below the floor", 1 << 62, 1, 16 << 20},
		{"configured over the max", 1 << 62, 1 << 20, 64 << 30},
		{"Helm GOMEMLIMIT=900MiB: an eighth", 900 << 20, 0, 900 << 20 / 8},
		{"standalone GOMEMLIMIT=256MiB: an eighth", 256 << 20, 0, 32 << 20},
		{"GOMEMLIMIT=64MiB: the floor", 64 << 20, 0, 16 << 20},
		{"GOMEMLIMIT=4GiB: the default", 4 << 30, 0, 128 << 20},
		{"configured beats GOMEMLIMIT", 256 << 20, 200, 200 << 20},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			debug.SetMemoryLimit(tc.memLimit)
			if got := gzipInflightBytes(tc.mib); got != tc.want {
				t.Errorf("gzipInflightBytes(%d) under GOMEMLIMIT %d = %d, want %d", tc.mib, tc.memLimit, got, tc.want)
			}
		})
	}
}
