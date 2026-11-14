package ingestbench

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
)

// M1: each sentinel's read-visible lag is bracketed from the SCHEDULED send:
// the upper bound ends at the first poll that saw it, the lower bound starts at
// the later of its actual send and the last poll that missed it. Sentinels of
// requests that were not fully acknowledged are dropped, unseen ones counted.
func TestSentinelLagIsBracketedFromTheScheduledSend(t *testing.T) {
	c := testConfig()
	c.Duration = 240 * time.Second // 10 s windows
	start := time.Unix(1700000000, 0)
	at := func(ms int) time.Time { return start.Add(time.Duration(ms) * time.Millisecond) }
	s := newRunState(c, start, false)
	s.registerSentinel(0, at(0), at(50), at(-100))       // seen, missed at 300
	s.registerSentinel(100, at(1000), at(1000), at(900)) // seen at once, never missed
	s.registerSentinel(200, at(12000), at(12500), at(11900))
	s.registerSentinel(300, at(13000), at(13000), at(12900)) // refused
	s.sentinels[0].lastMiss, s.sentinels[0].seenAt = at(300), at(400)
	s.sentinels[100].seenAt = at(1250)
	for _, seq := range []int64{0, 100, 200} {
		s.sentinelFinished(seq, true)
	}
	s.sentinelFinished(300, false)
	r := s.lagResult(c, start)
	if r.Registered != 4 || r.DroppedNotAcknowledged != 1 || r.Sampled != 3 || r.Unseen != 1 {
		t.Fatalf("sentinel counts: %+v", r)
	}
	// Upper from scheduled: 400 and 250 ms; lower: 300 and 0 ms; from the
	// actual send: 350 and 250 ms.
	// 250 ms is reported as the upper edge of its bucket (within 1/128 above).
	b250 := latencyBucketMicros(250_000)
	if r.FromScheduled.Count != 2 || r.FromScheduled.Max != 400 || r.FromScheduled.P50 != latencyEdge(b250) || latencyEdge(b250) > 250*(1+1.0/128) {
		t.Fatalf("upper bound from scheduled: %+v", r.FromScheduled)
	}
	if r.LowerFromScheduled.Max != 300 || r.LowerFromScheduled.P50 != 0 {
		t.Fatalf("lower bound from scheduled: %+v", r.LowerFromScheduled)
	}
	if r.Latency.Max != 350 || r.Latency.P50 != latencyEdge(b250) {
		t.Fatalf("upper bound from the actual send: %+v", r.Latency)
	}
	// Windows by scheduled send: [0,10) holds two seen, [10,20) one unseen.
	if len(r.Windows) != 2 || r.Windows[0].Seen != 2 || r.Windows[0].Unseen != 0 || r.Windows[1].Seen != 0 || r.Windows[1].Unseen != 1 || r.WindowSeconds != 10 {
		t.Fatalf("lag windows: %+v", r.Windows)
	}
	if len(r.Buckets) != 2 || r.Buckets[0] != (HistogramBucket{LowerMS: latencyLowerEdge(b250), UpperMS: latencyEdge(b250), Count: 1}) || r.Buckets[0].LowerMS > 250 {
		t.Fatalf("raw buckets: %+v", r.Buckets)
	}
}

// M1: the backlog summary separates backlog growth from throughput: the slope
// of buffered rows, the drain after the load, collector restarts (counter
// resets), unreachable windows and insert stalls.
func TestBacklogSummaryReportsGrowthDrainRestartsAndStalls(t *testing.T) {
	var samples []CollectorSample
	inserted := uint64(0)
	for i := 0; i <= 20; i++ {
		inserted += 10
		samples = append(samples, CollectorSample{ElapsedS: float64(i), OK: true, BufferedRows: int64(5 * i), MaxPendingRows: 100, RowsInserted: inserted})
	}
	// Restart: unreachable, then the counters start again from zero.
	samples = append(samples, CollectorSample{ElapsedS: 21, Error: "collector health request failed (URL omitted)"})
	samples = append(samples, CollectorSample{ElapsedS: 22, OK: true, BufferedRows: 40, MaxPendingRows: 100, RowsInserted: 5})
	// A stall: rows buffered, nothing inserted for 6 s.
	for i := 23; i <= 29; i++ {
		samples = append(samples, CollectorSample{ElapsedS: float64(i), OK: true, BufferedRows: 40, MaxPendingRows: 100, RowsInserted: 5})
	}
	samples = append(samples, CollectorSample{ElapsedS: 30, OK: true, BufferedRows: 0, MaxPendingRows: 100, RowsInserted: 45})
	r := summariseBacklog(samples, int64(len(samples)), 1, time.Second, 20, 10, 5*time.Second)
	if r.Status != "measured-with-gaps" || r.Samples != int64(len(samples)) || r.FailedSamples != 1 {
		t.Fatalf("status: %+v", r)
	}
	if math.Abs(r.SlopeRowsPerSecond-5) > 1e-9 || math.Abs(r.SecondHalfSlopeRowsPerSecond-5) > 1e-9 || r.SlopeSamples != 21 || r.MaxBufferedRows != 100 {
		t.Fatalf("growth: slope %v second half %v over %d samples, max %d", r.SlopeRowsPerSecond, r.SecondHalfSlopeRowsPerSecond, r.SlopeSamples, r.MaxBufferedRows)
	}
	// At 91+ buffered rows one more 10-span batch would not fit: t = 19, 20.
	if r.AtBudgetSamples != 2 || math.Abs(r.InsertRateDuringLoad-10) > 1e-9 {
		t.Fatalf("budget samples %d, insert rate %v", r.AtBudgetSamples, r.InsertRateDuringLoad)
	}
	if !r.Drained || r.DrainSeconds != 10 || r.FinalBufferedRow != 0 {
		t.Fatalf("drain: %+v", r)
	}
	// The first sample's lifetime count predates the observation: 20 x 10
	// during the load, then the restarted writer's 5 and its later 40.
	if r.CounterResets != 1 || r.RowsInserted != 200+5+40 || len(r.Unreachable) != 1 || r.Unreachable[0] != (Window{StartS: 21, EndS: 22, Samples: 1}) {
		t.Fatalf("restart accounting: resets %d inserted %d unreachable %+v", r.CounterResets, r.RowsInserted, r.Unreachable)
	}
	if len(r.InsertStalls) != 1 || r.InsertStalls[0].StartS != 22 || r.InsertStalls[0].EndS != 29 {
		t.Fatalf("insert stalls: %+v", r.InsertStalls)
	}
	// A /healthz that predates backlog reporting is a failed sample, not zero.
	if s := ParseCollectorHealth(200, []byte(`{"status":"ok","stats":{"RowsInserted":3}}`)); s.OK || s.Error == "" {
		t.Fatalf("old /healthz parsed as a sample: %+v", s)
	}
}

// BM-T03: the server-side insert lag is measured for every stored span, the
// generator-to-server clock offset is measured and corrected, and the measure
// is falsifiable: a forged created_at or an uncorrected skewed clock moves the
// distribution, created_at before the scheduled send is counted, and a failing
// lag query never blocks the exact-ID verification.
func TestDurableLagCoversEveryStoredSpanAndIsFalsifiable(t *testing.T) {
	run := func(t *testing.T, mutate func(*mockStorage)) Result {
		t.Helper()
		c := mockRunConfig()
		c.DirectProbes.Enabled, c.VerifyQueryRetries = false, 0
		store := &mockStorage{cfg: c}
		mutate(store)
		ch := httptest.NewServer(http.HandlerFunc(store.serve))
		defer ch.Close()
		c.ClickHouseURL, c.Endpoint = ch.URL, startFixtureCollector(t, ch.URL)
		store.cfg = c
		r, err := Run(context.Background(), c, Options{})
		if err != nil {
			t.Fatal(err)
		}
		if !r.Verification.Complete || r.Verification.DistinctExpected != 12 {
			t.Fatalf("verification: %+v", r.Verification)
		}
		return r
	}
	t.Run("honest", func(t *testing.T) {
		r := run(t, func(*mockStorage) {})
		d := r.Verification.DurableLag
		if d.Status != "measured" || d.Rows != 12 || d.Lag.Count != 12 || d.NegativeRows != 0 || !d.ClockMeasured || len(d.Buckets) == 0 {
			t.Fatalf("durable lag: %+v", d)
		}
		limit := float64(clockLimit) / float64(time.Millisecond)
		if !r.Clock.Start.Measured || !r.Clock.End.Measured || r.Clock.Limited != (math.Abs(r.Clock.Start.OffsetMS) > limit || r.Clock.Start.UncertaintyMS > limit ||
			math.Abs(r.Clock.End.OffsetMS) > limit || r.Clock.End.UncertaintyMS > limit) {
			t.Fatalf("clock: %+v", r.Clock)
		}
		if d.Lag.P50 >= 3000 {
			t.Fatalf("an in-process insert took %v ms from its scheduled send", d.Lag.P50)
		}
	})
	t.Run("forged-created-at", func(t *testing.T) {
		d := run(t, func(m *mockStorage) { m.createdAtShift = 3 * time.Second }).Verification.DurableLag
		// Every true lag is at least 3 s; the durable lag is a lower bound, so its
		// quantiles are bucket lower edges (at most 1/128 below the value).
		if d.Status != "measured" || d.Lag.Count != 12 || d.Lag.P50 < 3000*(1-1.0/128) || d.Lag.Max < 3000 {
			t.Fatalf("a created_at 3 s late did not move the distribution: %+v", d)
		}
	})
	t.Run("skewed-server-clock", func(t *testing.T) {
		// The server clock runs 2 s ahead: created_at and now64 both move. The
		// measured offset is applied, so the lag stays where it was, and the
		// result is flagged as clock-limited.
		r := run(t, func(m *mockStorage) { m.clockSkew = 2 * time.Second })
		d := r.Verification.DurableLag
		if !r.Clock.Limited || math.Abs(r.Clock.Start.OffsetMS-2000) > 500 || math.Abs(d.ClockOffsetMS-2000) > 500 {
			t.Fatalf("skew not measured: clock %+v, applied %v", r.Clock, d.ClockOffsetMS)
		}
		if d.Status != "measured" || d.Lag.Count != 12 || d.Lag.P50 >= 2000 {
			t.Fatalf("skew not corrected: %+v", d)
		}
	})
	t.Run("created-before-scheduled", func(t *testing.T) {
		d := run(t, func(m *mockStorage) { m.createdAtShift = -10 * time.Second }).Verification.DurableLag
		if d.Status != "measured" || d.Rows != 12 || d.NegativeRows != 12 || d.Lag.Count != 0 {
			t.Fatalf("created_at before the scheduled send not counted: %+v", d)
		}
	})
	t.Run("query-failure", func(t *testing.T) {
		r := run(t, func(m *mockStorage) { m.failDurable = true })
		if d := r.Verification.DurableLag; d.Status != "errors" || !strings.Contains(d.Method, "MEMORY_LIMIT_EXCEEDED") || r.Status != "measured-profile-complete" {
			t.Fatalf("durable lag failure: status %s, %+v", r.Status, d)
		}
	})
}

// pausingProxy forwards OTLP requests to the collector, except that every
// request arriving during one pause window is held until the window ends: an
// injected collector stall (a stop-the-world pause) of exactly `pause`, starting
// with the first request that arrives `after` the first request.
type pausingProxy struct {
	target       string
	after, pause time.Duration
	mu           sync.Mutex
	first, until time.Time
	held         atomic.Int64
}

func (p *pausingProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	now := time.Now()
	p.mu.Lock()
	if p.first.IsZero() {
		p.first = now
	}
	if p.until.IsZero() && now.Sub(p.first) >= p.after {
		p.until = now.Add(p.pause)
	}
	until := p.until
	p.mu.Unlock()
	if now.Before(until) {
		p.held.Add(1)
		time.Sleep(time.Until(until))
	}
	req, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, p.target, bytes.NewReader(body))
	req.Header = r.Header.Clone()
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		http.Error(w, "collector unreachable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// BM-T09: a 1.4 s collector stall at 100 one-span requests/s. Timed from the actual send
// (what a closed-loop client reports) only the two stalled requests are slow, so
// p99 stays low; timed from the scheduled send every request the stall delayed
// counts, so p99 shows the stall. The test fails if the corrected figure were
// computed from the actual send.
func TestCoordinatedOmissionIsCorrectedFromTheScheduledSend(t *testing.T) {
	c := mockRunConfig()
	c.Rate, c.Duration, c.BatchSize, c.Concurrency, c.VerifyChunk, c.Payload = 100, 3*time.Second, 1, 2, 1000, "small"
	// The mock answers sentinel polls by scanning every row; under the race
	// detector that is slow, so give the polls time to see the sentinels.
	c.DirectProbes.Enabled, c.Settle = false, 2*time.Second
	store := &mockStorage{cfg: c}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	proxy := &pausingProxy{target: startFixtureCollector(t, ch.URL), after: 500 * time.Millisecond, pause: 1400 * time.Millisecond}
	front := httptest.NewServer(proxy)
	defer front.Close()
	c.ClickHouseURL, c.Endpoint = ch.URL, front.URL+"/v1/traces"
	store.cfg = c
	r, err := Run(context.Background(), c, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if held := proxy.held.Load(); held < 1 || held > int64(c.Concurrency) {
		t.Fatalf("the stall held %d requests, want 1..%d", held, c.Concurrency)
	}
	u, k := r.RequestLatency, r.RequestLatencyFromScheduled
	if u.Count != k.Count || u.Count < 200 {
		t.Fatalf("latency populations: %d uncorrected, %d corrected; counters %+v stop %s", u.Count, k.Count, r.Counters, r.StopReason)
	}
	if u.Max < 1400 || k.Max < 1400 {
		t.Fatalf("the stalled request was not measured: uncorrected max %v, corrected max %v", u.Max, k.Max)
	}
	if u.P99 >= 1000 || k.P99 < 1300 {
		t.Fatalf("p99 uncorrected %v ms (want < 1000), corrected %v ms (want >= 1300)", u.P99, k.P99)
	}
	t.Logf("1.4 s stall at %d spans/s: request p99 %.0f ms from the actual send, %.0f ms from the scheduled send; %d late starts; sentinel read-visible upper p99 %.0f ms",
		c.Rate, u.P99, k.P99, r.Schedule.LateStartRequests, r.Lag.FromScheduled.P99)
	// The schedule shows the delay too: about 140 requests started late.
	if s := r.Schedule; s.LateStartRequests < 50 || s.StartDelay.Max < 1300 || s.LateStartRequestsOver1s < 1 {
		t.Fatalf("schedule: %+v", s)
	}
	// Read-visible lag is also timed from the scheduled send: the stalled
	// request's sentinel (polled first, being the oldest) shows the stall.
	if l := r.Lag; l.Sampled < 200 || l.FromScheduled.Count == 0 || l.FromScheduled.Max < 1400 || l.FromScheduled.P99 < 1300 {
		t.Fatalf("sentinel lag from scheduled: %d acknowledged sentinels, %+v", l.Sampled, l.FromScheduled)
	}
	if !r.Verification.Complete || r.Verification.Missing != 0 || r.Verification.MissingBy.AcknowledgedMissing != 0 {
		t.Fatalf("verification: %+v", r.Verification)
	}
}

// restartableCollector is the real in-process collector (receiver, converter,
// writer; PostgreSQL authentication bypassed) on fixed addresses, with an admin
// /healthz in the production shape, so a harness can stop it gracefully and
// start a new one on the same ports in the middle of a run.
type restartableCollector struct {
	t                             *testing.T
	wcfg                          chwriter.Config
	sc                            server.Config
	httpAddr, grpcAddr, adminAddr string

	mu     sync.Mutex
	cancel context.CancelFunc
	done   chan error
	writer *chwriter.Writer
	admin  *http.Server
	starts int
}

func newRestartableCollector(t *testing.T, wcfg chwriter.Config, sc server.Config) *restartableCollector {
	t.Helper()
	if wcfg.RequestTimeout == 0 {
		wcfg.RequestTimeout = 5 * time.Second
	}
	if wcfg.MaxRetries == 0 {
		wcfg.MaxRetries, wcfg.InitialBackoff, wcfg.MaxBackoff = 1, time.Millisecond, time.Millisecond
	}
	wcfg.DeadLetterFile = t.TempDir() + "/dl.jsonl"
	if sc.BatchMaxRows == 0 {
		sc.BatchMaxRows, sc.BatchMaxAge = 8, 20*time.Millisecond
	}
	rc := &restartableCollector{t: t, wcfg: wcfg, sc: sc, httpAddr: freeAddr(t), grpcAddr: freeAddr(t), adminAddr: freeAddr(t)}
	rc.sc.HTTPAddr, rc.sc.GRPCAddr = rc.httpAddr, rc.grpcAddr
	if err := rc.start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := rc.stop(); err != nil {
			t.Error(err)
		}
	})
	return rc
}

func (rc *restartableCollector) start() error {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if rc.cancel != nil {
		return nil
	}
	writer, err := chwriter.New(rc.wcfg)
	if err != nil {
		return err
	}
	srv := server.New(rc.sc, writer, nil, nil, nil, server.WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- srv.Run(ctx) }()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "stats": writer.Snapshot(), "admission": srv.Admission(), "limits": srv.Limits(), "ingest": srv.IngestStats(),
			"writer": writer.Policy(), "build": map[string]any{"configured_revision": "in-process-test-build"},
			"runtime": map[string]any{"num_cpu": runtime.NumCPU(), "gomaxprocs": runtime.GOMAXPROCS(0)}})
	})
	var lis net.Listener
	deadline := time.Now().Add(5 * time.Second)
	for {
		if lis, err = net.Listen("tcp", rc.adminAddr); err == nil {
			break
		}
		if time.Now().After(deadline) {
			cancel()
			return err
		}
		time.Sleep(10 * time.Millisecond)
	}
	admin := &http.Server{Handler: mux, ReadHeaderTimeout: time.Second}
	go func() { _ = admin.Serve(lis) }()
	for _, addr := range []string{rc.httpAddr, rc.grpcAddr} {
		for {
			conn, err := net.DialTimeout("tcp", addr, 20*time.Millisecond)
			if err == nil {
				conn.Close()
				break
			}
			if time.Now().After(deadline) {
				cancel()
				return err
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	rc.cancel, rc.done, rc.writer, rc.admin = cancel, done, writer, admin
	rc.starts++
	return nil
}

// stop shuts the collector down gracefully: it drains its buffer once (the
// production shutdown path) before the listeners close.
func (rc *restartableCollector) stop() error {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	if rc.cancel == nil {
		return nil
	}
	rc.cancel()
	var err error
	select {
	case err = <-rc.done:
	case <-time.After(20 * time.Minute):
		// The final drain waits for in-flight inserts (minutes on a slow
		// shared ClickHouse).
		return context.DeadlineExceeded
	}
	_ = rc.admin.Close()
	_ = rc.writer.Close()
	rc.cancel = nil
	// A graceful stop returns the cancellation.
	if err != nil && !errors.Is(err, context.Canceled) {
		return err
	}
	return nil
}

func (rc *restartableCollector) endpoint() string { return "http://" + rc.httpAddr + "/v1/traces" }
func (rc *restartableCollector) health() string   { return "http://" + rc.adminAddr + "/healthz" }

// pauseProxy sits between the collector's writer and ClickHouse. While paused it
// holds every request (as a stopped ClickHouse process holds an accepted
// connection) and forwards it on resume.
type pauseProxy struct {
	proxy *httputil.ReverseProxy
	mu    sync.Mutex
	gate  chan struct{}
	held  atomic.Int64
}

func newPauseProxy(t *testing.T, target string) *httptest.Server {
	t.Helper()
	u, err := url.Parse(target)
	if err != nil {
		t.Fatal(err)
	}
	return httptest.NewServer(&pauseProxy{proxy: httputil.NewSingleHostReverseProxy(u)})
}

func (p *pauseProxy) pause(context.Context) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.gate == nil {
		p.gate = make(chan struct{})
	}
	return nil
}

func (p *pauseProxy) resume(context.Context) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.gate != nil {
		close(p.gate)
		p.gate = nil
	}
	return nil
}

func (p *pauseProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p.mu.Lock()
	gate := p.gate
	p.mu.Unlock()
	if gate != nil {
		p.held.Add(1)
		select {
		case <-gate:
		case <-r.Context().Done():
			return
		}
	}
	p.proxy.ServeHTTP(w, r)
}

// recoveryConfig is a CI-sized recovery run: 40 spans/s for 4 s in requests of
// four, the collector backlog sampled every 100 ms.
func recoveryConfig(collector *restartableCollector) Config {
	c := mockRunConfig()
	c.Rate, c.Duration, c.BatchSize, c.VerifyChunk, c.Payload = 40, 4*time.Second, 4, 64, "small"
	c.Settle, c.Endpoint, c.CollectorHealthURL, c.CollectorPollInterval = 1500*time.Millisecond, collector.endpoint(), collector.health(), 100*time.Millisecond
	c.DirectProbes.Enabled = false
	return c
}

// requireRecovered checks the M2 acceptance: no acknowledged span lost by the
// exact-ID verification, duplicates counted, the backlog drained, and a finite
// recovery time for the fault.
func requireRecovered(t *testing.T, r Result, name string) FaultResult {
	t.Helper()
	if r.Recovery == nil || len(r.Recovery.Faults) != 1 {
		t.Fatalf("no recovery result: %+v", r.Recovery)
	}
	rec, f := r.Recovery, r.Recovery.Faults[0]
	if f.Name != name || f.Source != "harness-injected" || f.Error != "" || f.EndS <= f.StartS {
		t.Fatalf("fault record: %+v", f)
	}
	v := r.Verification
	if !v.Complete || v.MissingBy.AcknowledgedMissing != 0 || v.DataLossDetected || v.ContractViolation || v.UnexpectedRows != 0 {
		t.Fatalf("verification after %s: %+v", name, v)
	}
	if !rec.Complete || rec.AcknowledgedMissing != 0 || rec.VisibleDuplicates != v.DuplicateRows || (rec.LossWithinBound != nil && !*rec.LossWithinBound) {
		t.Fatalf("recovery integrity: %+v", rec)
	}
	if f.AcknowledgedMissing == nil || *f.AcknowledgedMissing != 0 {
		t.Fatalf("acknowledged loss attributed to the fault: %+v", f.AcknowledgedMissing)
	}
	if !f.Recovered || f.RecoverySeconds == nil || *f.RecoverySeconds < 0 || f.FirstAckAfterEndS == nil || f.EarlierSentinelsUnseen != 0 || f.EarlierSentinels == 0 {
		t.Fatalf("%s did not recover: %+v", name, f)
	}
	if !r.Backlog.Drained || r.Backlog.FinalBufferedRow != 0 || r.Backlog.Samples < 10 {
		t.Fatalf("backlog did not drain: %+v", r.Backlog)
	}
	if r.Status != "measured-recovery-complete" || !rec.Passed() {
		t.Fatalf("recovery run reported as %s", r.Status)
	}
	opt := func(v *float64) string {
		if v == nil {
			return "n/a"
		}
		return strconv.FormatFloat(*v, 'f', 3, 64)
	}
	t.Logf("%s (%s, ClickHouse %s): fault %.3f-%.3fs; recovery %ss = max(first ack %ss, backlog at baseline %ss, earlier sentinels visible %ss); "+
		"backlog max %d rows, slope %.1f rows/s; requests during fault %+v; acknowledged-missing %d; visible duplicates %d; ambiguous persisted %d; read-visible upper p99 %.0f ms",
		name, r.Config.Environment, r.Provenance.ClickHouse.Version, f.StartS, f.EndS, opt(f.RecoverySeconds), opt(f.FirstAckAfterEndS), opt(f.BacklogDrainedAfterEnd), opt(f.EarlierVisibleAfterEndS),
		r.Backlog.MaxBufferedRows, r.Backlog.SlopeRowsPerSecond, f.RequestsScheduledDuring, rec.AcknowledgedMissing, rec.VisibleDuplicates, rec.AmbiguousPersisted, r.Lag.FromScheduled.P99)
	return f
}

// M2: the collector is restarted (graceful stop, new process on the same
// ports) in the middle of the load. Requests it could not answer are ambiguous,
// never acknowledged; every acknowledged span is stored; the backlog sampler
// sees the outage and the counter reset; the recovery time is reported.
func TestRecoveryFromACollectorRestartLosesNoAcknowledgedSpan(t *testing.T) {
	store := &mockStorage{}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	// This case also asserts a finite crash-loss bound, so opt into a budget.
	collector := newRestartableCollector(t, chwriter.Config{URL: ch.URL, Table: "spans", Database: "default"}, server.Config{IngestBudgetMiB: 16})
	c := recoveryConfig(collector)
	c.ClickHouseURL = ch.URL
	store.cfg = c
	var telemetry bytes.Buffer
	r, err := Run(context.Background(), c, Options{ResourceWriter: &telemetry, Faults: []Fault{{Name: "collector-restart", At: 1500 * time.Millisecond, Duration: 800 * time.Millisecond,
		Inject: func(context.Context) error { return collector.stop() }, Restore: func(context.Context) error { return collector.start() }}}})
	if err != nil {
		t.Fatal(err)
	}
	f := requireRecovered(t, r, "collector-restart")
	if collector.starts != 2 {
		t.Fatalf("collector started %d times", collector.starts)
	}
	// While it was down nothing was acknowledged; its requests are ambiguous.
	if f.RequestsScheduledDuring.Ambiguous == 0 || r.Counters.Ambiguous == 0 || r.Counters.Attempted != r.PlannedSpans {
		t.Fatalf("outage not visible in the outcomes: %+v %+v", f.RequestsScheduledDuring, r.Counters)
	}
	if r.Backlog.CounterResets < 1 || len(r.Backlog.Unreachable) < 1 {
		t.Fatalf("restart not visible in the backlog series: %+v", r.Backlog)
	}
	requireTelemetry(t, telemetry.Bytes(), "collector-restart")
}

// F6: a fault counts as recovered only when all three components were observed.
// Without the collector backlog (no health URL), or without a backlog sample
// before the fault (no baseline), the drain is unknown and the fault is not
// recovered, whatever the acknowledgements and sentinels say.
func TestRecoveryNeedsAnObservedBacklogDrain(t *testing.T) {
	ack, visible, drained, baseline := f64(0.2), f64(0.1), f64(0.3), int64(5)
	full := FaultResult{FirstAckAfterEndS: ack, EarlierSentinels: 3, EarlierVisibleAfterEndS: visible, BacklogBaselineRows: &baseline, BacklogDrainedAfterEnd: drained}
	if got, ok, why := recoveryTime(full, true); !ok || got == nil || *got != 0.3 || why != "" {
		t.Fatalf("all components observed: %v %v %q", got, ok, why)
	}
	if _, ok, why := recoveryTime(full, false); ok || !strings.Contains(why, "not observed") {
		t.Fatalf("no backlog samples: recovered=%v %q", ok, why)
	}
	noBaseline := full
	noBaseline.BacklogBaselineRows, noBaseline.BacklogDrainedAfterEnd = nil, nil
	if _, ok, why := recoveryTime(noBaseline, true); ok || !strings.Contains(why, "baseline") {
		t.Fatalf("no pre-fault sample: recovered=%v %q", ok, why)
	}
	notDrained := full
	notDrained.BacklogDrainedAfterEnd = nil
	if _, ok, why := recoveryTime(notDrained, true); ok || !strings.Contains(why, "did not return") {
		t.Fatalf("backlog not drained: recovered=%v %q", ok, why)
	}

	// End to end: the restart case without a health URL is not a recovery.
	store := &mockStorage{}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	collector := newRestartableCollector(t, chwriter.Config{URL: ch.URL, Table: "spans", Database: "default"}, server.Config{})
	c := recoveryConfig(collector)
	c.ClickHouseURL, c.CollectorHealthURL = ch.URL, ""
	store.cfg = c
	r, err := Run(context.Background(), c, Options{Faults: []Fault{{Name: "collector-restart", At: 1500 * time.Millisecond, Duration: 800 * time.Millisecond,
		Inject: func(context.Context) error { return collector.stop() }, Restore: func(context.Context) error { return collector.start() }}}})
	if err != nil {
		t.Fatal(err)
	}
	if r.Recovery == nil || len(r.Recovery.Faults) != 1 {
		t.Fatalf("no recovery result: %+v", r.Recovery)
	}
	f := r.Recovery.Faults[0]
	if f.Recovered || f.RecoverySeconds != nil || !strings.Contains(f.NotRecoveredReason, "not observed") || r.Recovery.Passed() || r.Status == "measured-recovery-complete" {
		t.Fatalf("a recovery without any backlog observation was accepted: status %s fault %+v", r.Status, f)
	}
}

// M2: ClickHouse is paused (every writer request held) for 1.5 s while the
// collector runs with a small byte budget. Row leases build up, and the
// collector refuses with a retry hint instead of dropping, the held inserts
// complete on resume, the backlog drains and no acknowledged span is lost.
func TestRecoveryFromAClickHousePauseLosesNoAcknowledgedSpan(t *testing.T) {
	store := &mockStorage{}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	front := newPauseProxy(t, ch.URL)
	defer front.Close()
	proxy := front.Config.Handler.(*pauseProxy)
	collector := newRestartableCollector(t, chwriter.Config{URL: front.URL, Table: "spans", Database: "default"}, server.Config{IngestBudgetMiB: 2, IngestMaxWait: time.Millisecond, InsertConcurrency: 1})
	c := recoveryConfig(collector)
	c.ClickHouseURL = ch.URL
	store.cfg = c
	r, err := Run(context.Background(), c, Options{Faults: []Fault{{Name: "clickhouse-pause", At: time.Second, Duration: 1500 * time.Millisecond, Inject: proxy.pause, Restore: proxy.resume}}})
	if err != nil {
		t.Fatal(err)
	}
	f := requireRecovered(t, r, "clickhouse-pause")
	if proxy.held.Load() == 0 {
		t.Fatal("no writer request was held by the pause")
	}
	if f.BacklogPeakRows == nil || *f.BacklogPeakRows == 0 || f.BacklogDrainedAfterEnd == nil || r.Backlog.MaxBufferedRows == 0 || r.Backlog.PeakWorkBytes == 0 {
		t.Fatalf("backlog did not build up against the budget: fault %+v backlog %+v", f, r.Backlog)
	}
	if f.RequestsScheduledDuring.RejectedRetryable == 0 || r.Counters.RequestOutcomes.RejectedWithRetryHint != r.Counters.RequestOutcomes.RejectedRetryable || r.Counters.Ambiguous != 0 {
		t.Fatalf("the full budget was not refused with retry hints: %+v %+v", f.RequestsScheduledDuring, r.Counters)
	}
	if r.Recovery.DeadLetteredRows == nil || *r.Recovery.DeadLetteredRows != 0 {
		t.Fatalf("held inserts were dead-lettered: %+v", r.Recovery.DeadLetteredRows)
	}
}

// requireTelemetry checks the 1 s series carries the five stages: scheduled,
// attempted, accepted and read-visible on progress lines and the collector's
// view of the durable stage on collector lines, plus the fault's two events.
func requireTelemetry(t *testing.T, data []byte, fault string) {
	t.Helper()
	kinds := map[string]int{}
	phases := map[string]bool{}
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var e telemetryEvent
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			t.Fatal(err)
		}
		kinds[e.Kind]++
		switch e.Kind {
		case "progress", "progress-final":
			if e.Counters == nil || e.ScheduledSpans == nil || e.SentinelsSeen == nil || e.InFlightSpans == nil || e.InFlightRequests == nil ||
				!progressIdentityHolds(*e.Counters, *e.InFlightSpans, *e.InFlightRequests, e.Kind == "progress-final") {
				t.Fatalf("progress line without the stages: %s", sc.Text())
			}
		case "collector":
			if e.Collector == nil {
				t.Fatalf("collector line without a sample: %s", sc.Text())
			}
		case "fault":
			if e.Fault != nil && e.Fault.Name == fault {
				phases[e.Fault.Phase] = true
			}
		}
	}
	if kinds["progress-final"] != 1 || kinds["collector"] < 10 || !phases["injected"] || !phases["restored"] {
		t.Fatalf("telemetry kinds %v, fault phases %v", kinds, phases)
	}
}
