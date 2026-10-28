package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// Tests for burst handling (R1 finding F1 on #3598): a busy answer comes
// within the maximum wait, both pools together, so exporters with a 10 s
// export budget can retry; several ClickHouse inserts run at once, each
// releasing its rows' leases only after its own insert (and every earlier
// one) has ended; shutdown drains them all. And (F2) HTTP refusals carry an
// OTLP Status body in the request's media type.

// With the production defaults a waiter is answered busy at the 2 s maximum
// wait even while a drain is under way: an SDK exporter's 10 s export budget
// then has room for about three attempts. (Before, it waited up to 10 s, the
// whole budget, so the exporter gave up without ever retrying.)
func TestPoolDefaultMaxWaitAnswersBusyWithinTwoSeconds(t *testing.T) {
	p := newMemPool("work", 100)
	if p.maxWait != defaultLeaseMaxWait || defaultLeaseMaxWait != 2*time.Second || p.wait != leaseWait {
		t.Fatalf("defaults: max wait %v (const %v), no-progress wait %v; want 2s, 2s, %v", p.maxWait, defaultLeaseMaxWait, p.wait, leaseWait)
	}
	held := holdLeases(t, p, 1, 100)[0]
	defer held.release()
	p.busy = func() bool { return true } // a drain is storing rows the whole time
	start := time.Now()
	_, err := p.acquire(context.Background(), 10)
	waited := time.Since(start)
	if !errors.Is(err, errPoolBusy) {
		t.Fatalf("acquire: %v, want errPoolBusy", err)
	}
	if waited < defaultLeaseMaxWait || waited > defaultLeaseMaxWait+time.Second {
		t.Errorf("busy after %v, want about the %v maximum wait", waited, defaultLeaseMaxWait)
	}
}

// A longer maximum wait (FI_INGEST_MAX_WAIT) still keeps a waiter in line
// through a drain that takes longer than the no-progress wait, as the 10 s
// default did.
func TestPoolLongerMaxWaitWaitsThroughASlowDrain(t *testing.T) {
	p := newMemPool("work", 100)
	p.setMaxWait(6 * time.Second)
	if p.wait != leaseWait {
		t.Fatalf("no-progress wait %v, want %v", p.wait, leaseWait)
	}
	held := holdLeases(t, p, 1, 100)[0]
	p.busy = func() bool { return true }
	timer := time.AfterFunc(3*time.Second, held.release)
	defer timer.Stop()
	defer held.release()
	start := time.Now()
	l, err := p.acquire(context.Background(), 100)
	if err != nil {
		t.Fatalf("acquire: %v after %v, want the lease when the 3 s drain ends", err, time.Since(start))
	}
	l.release()
	// A maximum wait under the no-progress wait caps it too.
	short := newMemPool("work", 100)
	short.setMaxWait(500 * time.Millisecond)
	if short.wait != 500*time.Millisecond {
		t.Errorf("no-progress wait %v, want capped at the 500ms maximum", short.wait)
	}
}

// One request waits at most its budget across both pools: time spent waiting
// for the wire pool is taken from what it may wait for the work pool, and a
// request whose budget is spent is busy at once.
func TestLeaseBudgetIsSharedByBothPools(t *testing.T) {
	const budget = 600 * time.Millisecond
	wire, work := newMemPool("wire", 100), newMemPool("work", 100)
	wire.setMaxWait(budget)
	work.setMaxWait(budget)
	work.busy = func() bool { return true }
	heldWire := holdLeases(t, wire, 1, 100)[0]
	heldWork := holdLeases(t, work, 1, 100)[0]
	defer heldWork.release()
	time.AfterFunc(400*time.Millisecond, heldWire.release)

	ctx := withLeaseBudget(context.Background(), budget)
	start := time.Now()
	wl, err := wire.acquire(ctx, 10)
	if err != nil {
		t.Fatalf("wire: %v", err)
	}
	defer wl.release()
	_, err = work.acquire(ctx, 10)
	waited := time.Since(start)
	if !errors.Is(err, errPoolBusy) {
		t.Fatalf("work: %v, want errPoolBusy", err)
	}
	if waited < budget-50*time.Millisecond || waited > budget+400*time.Millisecond {
		t.Errorf("busy after %v in all, want about the %v budget (not %v per pool)", waited, budget, budget)
	}
	before := work.timeouts.Load()
	start = time.Now()
	if _, err := work.acquire(ctx, 10); !errors.Is(err, errPoolBusy) {
		t.Fatalf("spent budget: %v, want errPoolBusy", err)
	}
	if d := time.Since(start); d > 50*time.Millisecond {
		t.Errorf("a request with no budget left waited %v, want an answer at once", d)
	}
	if work.timeouts.Load() != before+1 || work.waiting.Load() != 0 {
		t.Errorf("timeouts %d waiting %d, want %d and 0", work.timeouts.Load(), work.waiting.Load(), before+1)
	}
	// With room, a spent budget does not matter: nothing waits.
	heldWork.release()
	l, err := work.acquire(ctx, 10)
	if err != nil {
		t.Fatalf("free pool with a spent budget: %v", err)
	}
	l.release()
}

// Over OTLP/HTTP: a request that waits for the wire pool and then the work
// pool is answered 503 + Retry-After: 1 within one maximum wait, with an OTLP
// Status body; the same request succeeds once the pools are back.
func TestOTLPHTTPBusyWithinOneMaxWait(t *testing.T) {
	ch, w := newCHBodies(t)
	_ = ch
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour, IngestBudgetMiB: 96}, w, nil, nil, nil)
	if s.limits.maxWait != defaultLeaseMaxWait {
		t.Fatalf("max wait %v, want the %v default", s.limits.maxWait, defaultLeaseMaxWait)
	}
	s.work.busy = func() bool { return true }
	body := protoBody(t, makeTraces("burst-span", "51515151-5151-4151-8151-515151515151"))
	releaseWire := holdPool(t, s.wire, s.wire.size)
	releaseWork := holdPool(t, s.work, s.work.size)
	time.AfterFunc(1200*time.Millisecond, releaseWire) // 1.2 s on the wire pool, then the work pool

	start := time.Now()
	rec := postBody(s, "application/x-protobuf", false, nil, body)
	waited := time.Since(start)
	if rec.Code != http.StatusServiceUnavailable || rec.Header().Get("Retry-After") != "1" {
		t.Fatalf("status %d Retry-After %q, want 503 and 1 (%q)", rec.Code, rec.Header().Get("Retry-After"), rec.Body.String())
	}
	if waited < defaultLeaseMaxWait-100*time.Millisecond || waited > defaultLeaseMaxWait+700*time.Millisecond {
		t.Errorf("answered after %v, want about the %v maximum wait for both pools together", waited, defaultLeaseMaxWait)
	}
	if code, msg := decodeStatus(t, rec); code != codes.Unavailable || msg != rejectWorkBusy.msg {
		t.Errorf("Status %v %q, want UNAVAILABLE %q", code, msg, rejectWorkBusy.msg)
	}
	releaseWork()
	if rec := postBody(s, "application/x-protobuf", false, nil, body); rec.Code != http.StatusOK {
		t.Fatalf("after the pools freed up: %d (%s)", rec.Code, rec.Body.String())
	}
	s.drainNow(context.Background())
	if w, k := s.wire.inUse.Load(), s.work.inUse.Load(); w != 0 || k != 0 {
		t.Errorf("leased after the requests: wire %d work %d", w, k)
	}
}

// Over OTLP/gRPC the same: UNAVAILABLE within one maximum wait.
func TestGRPCBusyWithinOneMaxWait(t *testing.T) {
	s, conn, _ := startGRPCServer(t, Config{IngestBudgetMiB: 96, IngestMaxWait: time.Second}, nil)
	if s.limits.maxWait != time.Second || s.wire.maxWait != time.Second || s.work.maxWait != time.Second {
		t.Fatalf("max wait: limits %v wire %v work %v, want 1s (Config.IngestMaxWait)", s.limits.maxWait, s.wire.maxWait, s.work.maxWait)
	}
	s.work.busy = func() bool { return true }
	releaseWire := holdPool(t, s.wire, s.wire.size)
	releaseWork := holdPool(t, s.work, s.work.size)
	defer releaseWork()
	time.AfterFunc(600*time.Millisecond, releaseWire)
	start := time.Now()
	err := exportRaw(conn, context.Background(), emptySpans(t, 10))
	waited := time.Since(start)
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("Export: %v, want UNAVAILABLE", err)
	}
	if waited < 900*time.Millisecond || waited > 1700*time.Millisecond {
		t.Errorf("answered after %v, want about the 1s maximum wait for both pools together", waited)
	}
}

// decodeStatus decodes an OTLP/HTTP refusal's google.rpc.Status body in the
// response's media type.
func decodeStatus(t *testing.T, rec *httptest.ResponseRecorder) (codes.Code, string) {
	t.Helper()
	st := status.New(codes.OK, "").Proto()
	proto.Reset(st)
	var err error
	switch ct := rec.Header().Get("Content-Type"); ct {
	case "application/x-protobuf":
		err = proto.Unmarshal(rec.Body.Bytes(), st)
	case "application/json":
		err = protojson.Unmarshal(rec.Body.Bytes(), st)
	default:
		t.Fatalf("refusal Content-Type %q, want the request's OTLP media type", ct)
	}
	if err != nil {
		t.Fatalf("decode Status: %v (%q)", err, rec.Body.String())
	}
	return codes.Code(st.GetCode()), st.GetMessage()
}

// F2: an OTLP/HTTP refusal carries a google.rpc.Status in the request's media
// type, with the same message, status code and Retry-After as before, so
// stock exporters can show why (they showed only "413 Request Entity Too
// Large" for a text/plain body). A request in some other media type still
// gets text/plain.
func TestOTLPHTTPRefusalsCarryAnOTLPStatus(t *testing.T) {
	s := acceptingServer()
	s.wire.setMaxWait(100 * time.Millisecond)
	s.limits.maxWait = 100 * time.Millisecond
	okProto := protoBody(t, makeTraces("ok", "61616161-6161-4161-8161-616161616161"))
	cases := []struct {
		name, ct    string
		body        []byte
		hold        bool // the wire pool is full
		status      int
		code        codes.Code
		msg         string // a substring of the message
		retry       string
		wantPlainCT bool
	}{
		{"413 protobuf", "application/x-protobuf", make([]byte, defaultMaxBodyBytes+1), false, 413, codes.InvalidArgument, "FI_OTLP_MAX_BODY_MIB", "", false},
		{"413 JSON", "application/json", make([]byte, defaultMaxBodyBytes+1), false, 413, codes.InvalidArgument, "FI_OTLP_MAX_BODY_MIB", "", false},
		{"400 protobuf", "application/x-protobuf", []byte{0x0a, 0x05, 0x00}, false, 400, codes.InvalidArgument, "decode", "", false},
		{"400 JSON", "application/json", []byte(`{"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":7}]}]}]}`), false, 400, codes.InvalidArgument, "decode", "", false},
		{"503 protobuf", "application/x-protobuf", okProto, true, 503, codes.Unavailable, rejectWireBusy.msg, "1", false},
		{"503 JSON", "application/json", []byte(`{}`), true, 503, codes.Unavailable, rejectWireBusy.msg, "1", false},
		{"415 text/plain", "text/plain", okProto, false, 415, 0, "unsupported content type", "", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			release := func() {}
			if tc.hold {
				release = holdPool(t, s.wire, s.wire.size)
			}
			rec := postBody(s, tc.ct, false, nil, tc.body)
			release()
			if rec.Code != tc.status || rec.Header().Get("Retry-After") != tc.retry {
				t.Fatalf("status %d Retry-After %q; want %d %q (%q)", rec.Code, rec.Header().Get("Retry-After"), tc.status, tc.retry, rec.Body.String())
			}
			if tc.wantPlainCT {
				if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") || !strings.Contains(rec.Body.String(), tc.msg) {
					t.Errorf("Content-Type %q body %q, want text/plain naming %q", ct, rec.Body.String(), tc.msg)
				}
				return
			}
			if ct := rec.Header().Get("Content-Type"); ct != tc.ct {
				t.Errorf("Content-Type %q, want the request's %q", ct, tc.ct)
			}
			if code, msg := decodeStatus(t, rec); code != tc.code || !strings.Contains(msg, tc.msg) {
				t.Errorf("Status code %v message %q; want %v and a message containing %q", code, msg, tc.code, tc.msg)
			}
			if tc.ct == "application/json" {
				var m map[string]any
				if err := json.Unmarshal(rec.Body.Bytes(), &m); err != nil || m["message"] == nil || m["code"] == nil {
					t.Errorf("JSON Status %q, want an object with code and message: %v", rec.Body.String(), err)
				}
			}
		})
	}
}

// blockingCH is a stub ClickHouse whose span inserts each wait until the test
// lets the batch they carry (marked "batch-<name>") through.
type blockingCH struct {
	mu       sync.Mutex
	gates    map[string]chan struct{}
	inFlight atomic.Int32
	maxSeen  atomic.Int32
	started  chan string
	rows     map[string]int
}

func newBlockingCH(t *testing.T) (*blockingCH, *chwriter.Writer) {
	t.Helper()
	b := &blockingCH{gates: map[string]chan struct{}{}, started: make(chan string, 1024), rows: map[string]int{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if insertTable(r) != "spans" {
			w.WriteHeader(200)
			return
		}
		n := b.inFlight.Add(1)
		for m := b.maxSeen.Load(); n > m && !b.maxSeen.CompareAndSwap(m, n); m = b.maxSeen.Load() {
		}
		name := ""
		if i := bytes.Index(body, []byte(`"batch-`)); i >= 0 {
			rest := body[i+len(`"batch-`):]
			name = string(rest[:bytes.IndexByte(rest, '"')])
		}
		b.started <- name
		<-b.gate(name)
		b.mu.Lock()
		for _, line := range bytes.Split(bytes.TrimSpace(body), []byte("\n")) {
			var row map[string]any
			if json.Unmarshal(line, &row) == nil {
				b.rows[fmt.Sprint(row["name"])]++
			}
		}
		b.mu.Unlock()
		b.inFlight.Add(-1)
		w.WriteHeader(200)
	}))
	t.Cleanup(srv.Close)
	w, err := chwriter.New(chwriter.Config{URL: srv.URL, MaxRetries: 1, InitialBackoff: time.Millisecond,
		MaxBackoff: time.Millisecond, RequestTimeout: 30 * time.Second, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	return b, w
}

func (b *blockingCH) gate(name string) chan struct{} {
	b.mu.Lock()
	defer b.mu.Unlock()
	g, ok := b.gates[name]
	if !ok {
		g = make(chan struct{})
		b.gates[name] = g
	}
	return g
}

func (b *blockingCH) open(name string) { close(b.gate(name)) }

func (b *blockingCH) waitStarted(t *testing.T, name string) {
	t.Helper()
	select {
	case got := <-b.started:
		if got != name {
			t.Fatalf("insert of batch %q started, want %q", got, name)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("no insert of batch %q started", name)
	}
}

func (b *blockingCH) stored(name string) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.rows[name]
}

// batchRows are n rows of batch name (stand-ins for converted spans).
func batchRows(name string, n int) []map[string]any {
	rows := make([]map[string]any, n)
	for i := range rows {
		rows[i] = map[string]any{"name": "batch-" + name, "i": i}
	}
	return rows
}

// parkBatch parks rows with a rows lease of size bytes, as a request does
// (enqueue, then adoptRows), and wakes a flusher.
func parkBatch(t *testing.T, s *Server, name string, size int64) *lease {
	t.Helper()
	l, err := s.work.acquire(context.Background(), size)
	if err != nil {
		t.Fatal(err)
	}
	s.enqueue(batchRows(name, 10), nil)
	s.adoptRows(l)
	s.kickFlush()
	return l
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	for deadline := time.Now().Add(5 * time.Second); !cond(); time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
	}
}

// startFlushers runs the server's flushers as Run would, without listeners.
func startFlushers(t *testing.T, s *Server) {
	t.Helper()
	s.wg.Add(1)
	go s.flushLoop()
	t.Cleanup(func() {
		select {
		case <-s.stopCh:
		default:
			close(s.stopCh)
		}
		s.wg.Wait()
	})
}

// Up to InsertConcurrency inserts run at once, and each drain's rows lease is
// released only after its own insert, and every earlier drain's, has ended:
// never while ClickHouse may still be reading its rows.
func TestConcurrentInsertsReleaseLeasesOnlyAfterTheirInserts(t *testing.T) {
	ch, w := newBlockingCH(t)
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour, IngestBudgetMiB: 96, InsertConcurrency: 3}, w, nil, nil, nil)
	if s.limits.inserts != 3 {
		t.Fatalf("inserts %d, want 3", s.limits.inserts)
	}
	startFlushers(t, s)
	const mb = 1 << 20
	for _, name := range []string{"a", "b", "c"} {
		parkBatch(t, s, name, mb)
		ch.waitStarted(t, name) // each drain takes exactly one batch
	}
	if got := ch.maxSeen.Load(); got != 3 {
		t.Fatalf("%d inserts ran at once, want 3", got)
	}
	if got := s.IngestStats().InsertsInFlight; got != 3 {
		t.Errorf("inserts in flight %d, want 3", got)
	}
	if u := s.work.inUse.Load(); u != 3*mb {
		t.Fatalf("%d work bytes leased while every insert runs, want %d", u, 3*mb)
	}
	// A fourth batch waits for a free flusher.
	parkBatch(t, s, "d", mb)
	select {
	case got := <-ch.started:
		t.Fatalf("insert of %q started with every flusher busy", got)
	case <-time.After(200 * time.Millisecond):
	}

	ch.open("c") // the last drain ends first
	ch.waitStarted(t, "d")
	waitFor(t, "c's insert to end", func() bool { return ch.stored("batch-c") == 10 })
	time.Sleep(50 * time.Millisecond)
	if u := s.work.inUse.Load(); u != 4*mb {
		t.Errorf("%d work bytes leased after c ended before a and b, want all %d: c's lease waits for the earlier drains", u, 4*mb)
	}
	ch.open("a")
	waitFor(t, "a's lease", func() bool { return s.work.inUse.Load() == 3*mb })
	ch.open("b") // b, then the already-ended c
	waitFor(t, "b's and c's leases", func() bool { return s.work.inUse.Load() == mb })
	ch.open("d")
	waitFor(t, "every lease", func() bool { return s.work.inUse.Load() == 0 })
	for _, name := range []string{"a", "b", "c", "d"} {
		if n := ch.stored("batch-" + name); n != 10 {
			t.Errorf("batch %s: %d rows stored, want 10", name, n)
		}
	}
}

// A request may park its rows lease just after the swap that took its rows
// (enqueue, then adoptRows, are separate steps): the lease then rides with the
// next drain. When that drain's insert ends first, the lease must still wait
// for the drain that holds its rows.
func TestLateParkedLeaseWaitsForTheDrainHoldingItsRows(t *testing.T) {
	ch, w := newBlockingCH(t)
	s := New(Config{BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour, IngestBudgetMiB: 96, InsertConcurrency: 2}, w, nil, nil, nil)
	startFlushers(t, s)
	late, err := s.work.acquire(context.Background(), 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	s.enqueue(batchRows("rows", 10), nil) // the request's rows...
	s.kickFlush()
	ch.waitStarted(t, "rows") // ...are swapped out and being inserted
	s.adoptRows(late)         // then it parks its lease, for the next drain
	s.enqueue(batchRows("next", 10), nil)
	s.kickFlush()
	ch.waitStarted(t, "next")
	ch.open("next")
	waitFor(t, "next's insert", func() bool { return ch.stored("batch-next") == 10 })
	time.Sleep(50 * time.Millisecond)
	if late.size() == 0 || s.work.inUse.Load() != 1<<20 {
		t.Fatalf("lease released (in use %d) while the insert holding its rows still runs", s.work.inUse.Load())
	}
	ch.open("rows")
	waitFor(t, "the lease", func() bool { return s.work.inUse.Load() == 0 })
}

// Shutdown waits for every flusher's insert and then drains what is left:
// every row parked before Run returns is stored exactly once, and every lease
// is back.
func TestShutdownDrainsEveryConcurrentInsert(t *testing.T) {
	var mu sync.Mutex
	stored := map[string]int{}
	var inFlight, maxSeen atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if insertTable(r) == "spans" {
			n := inFlight.Add(1)
			for m := maxSeen.Load(); n > m && !maxSeen.CompareAndSwap(m, n); m = maxSeen.Load() {
			}
			time.Sleep(150 * time.Millisecond) // a slow ClickHouse
			mu.Lock()
			for _, line := range bytes.Split(bytes.TrimSpace(body), []byte("\n")) {
				var row map[string]any
				if json.Unmarshal(line, &row) == nil {
					stored[fmt.Sprintf("%v/%v", row["name"], row["i"])]++
				}
			}
			mu.Unlock()
			inFlight.Add(-1)
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	w, err := chwriter.New(chwriter.Config{URL: srv.URL, MaxRetries: 1, InitialBackoff: time.Millisecond,
		MaxBackoff: time.Millisecond, RequestTimeout: 30 * time.Second, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	addr := testTCPAddr(t)
	s := New(Config{GRPCAddr: addr, BatchMaxRows: 20, BatchMaxAge: time.Hour, IngestBudgetMiB: 96, InsertConcurrency: 4}, w, nil, nil, nil)
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	if !waitPort(addr, 2*time.Second) {
		cancel()
		t.Fatalf("server didn't listen on %s", addr)
	}
	const batches = 40
	for i := 0; i < batches; i++ {
		l, err := s.work.acquire(context.Background(), 64<<10)
		if err != nil {
			t.Fatal(err)
		}
		s.enqueue(batchRows(fmt.Sprint(i), 25), nil) // over BatchMaxRows: kicks a flusher
		s.adoptRows(l)
		time.Sleep(15 * time.Millisecond) // batches arrive while inserts (150 ms) run
	}
	waitFor(t, "inserts to run at once", func() bool { return maxSeen.Load() >= 2 })
	cancel()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("Run did not return")
	}
	mu.Lock()
	defer mu.Unlock()
	if len(stored) != batches*25 {
		t.Errorf("%d distinct rows stored, want %d", len(stored), batches*25)
	}
	for k, n := range stored {
		if n != 1 {
			t.Errorf("row %s stored %d times", k, n)
		}
	}
	if u := s.work.inUse.Load(); u != 0 {
		t.Errorf("%d work bytes leased after shutdown", u)
	}
	if got := maxSeen.Load(); got > 4 {
		t.Errorf("%d inserts at once, over the 4 configured", got)
	}
}

// The default number of concurrent inserts follows the memory limit (two
// insert buffers each come out of the budget's allowance), and a configured
// one is capped.
func TestInsertConcurrencyAndMaxWaitLimits(t *testing.T) {
	cases := []struct {
		name    string
		cfg     Config
		mem     int64
		inserts int
		wait    time.Duration
	}{
		{"no memory limit", Config{}, 0, 3, 2 * time.Second},
		{"Standalone 512MiB", Config{}, 512 * mib, 3, 2 * time.Second},
		{"384MiB", Config{}, 384 * mib, 2, 2 * time.Second},
		{"256MiB", Config{}, 256 * mib, 1, 2 * time.Second},
		{"64MiB", Config{}, 64 * mib, 1, 2 * time.Second},
		{"configured", Config{InsertConcurrency: 5, IngestMaxWait: 3 * time.Second}, 512 * mib, 5, 3 * time.Second},
		{"capped", Config{InsertConcurrency: 1000, IngestMaxWait: time.Hour}, 512 * mib, maxInsertConcurrency, maxLeaseMaxWait},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			l := resolveIngestLimits(tc.cfg, tc.mem)
			if l.inserts != tc.inserts || l.maxWait != tc.wait {
				t.Errorf("inserts %d wait %v, want %d %v", l.inserts, l.maxWait, tc.inserts, tc.wait)
			}
			if tc.mem > 0 && tc.cfg.IngestBudgetMiB == 0 {
				fixed := int64(fixedAllowance) + 2*l.insertMax*int64(l.inserts)
				if want := max((tc.mem-fixed)/2, tc.mem/8); l.budget != want {
					t.Errorf("budget %d, want %d: half of what is left after %d inserts' buffers", l.budget, want, l.inserts)
				}
			}
		})
	}
	// Standalone (GOMEMLIMIT=512MiB), as documented: a 216 MiB budget, 144 MiB
	// of it the work pool, 8 MiB inserts, three at once.
	l := resolveIngestLimits(Config{}, 512*mib)
	if l.budget != 216*mib || l.work != 144*mib || l.insertMax != 8*mib || l.inserts != 3 {
		t.Errorf("Standalone: budget %d work %d insert %d x%d; want 216 MiB, 144 MiB, 8 MiB x3", l.budget>>20, l.work>>20, l.insertMax>>20, l.inserts)
	}
}
