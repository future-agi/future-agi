package server

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestPendingRowBudgetIncludesBlockedInflightInsert(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		once.Do(func() { close(entered) })
		<-release
	}))
	defer ch.Close()
	writer, err := chwriter.New(chwriter.Config{URL: ch.URL, MaxRetries: 1, RequestTimeout: 5 * time.Second, DeadLetterFile: t.TempDir() + "/dl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := New(Config{MaxPendingRows: 2}, writer, nil, nil, nil)
	if s.enqueue([]map[string]any{{"id": "a"}, {"id": "b"}}, nil) != enqueueAccepted {
		t.Fatal("initial batch rejected")
	}
	// Parked rows are both pending and buffered (what /healthz reports).
	if a := s.Admission(); a != (Admission{BufferedRows: 2, PendingRows: 2, MaxPendingRows: 2}) {
		t.Fatalf("admission of parked rows = %+v", a)
	}
	done := make(chan struct{})
	go func() { s.drainNow(context.Background()); close(done) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		close(release)
		t.Fatal("writer never blocked")
	}
	// Rows of the insert in progress are no longer pending but still buffered:
	// a stalled writer shows as backlog, not as an empty queue.
	if a := s.Admission(); a != (Admission{BufferedRows: 2, PendingRows: 0, MaxPendingRows: 2}) {
		close(release)
		t.Fatalf("admission during the insert = %+v", a)
	}
	// The old code swaps pend to nil before the insert. A cap on len(pend)
	// alone would let more rows in and is not a bounded in-flight budget.
	refused := s.enqueue([]map[string]any{{"id": "c"}}, nil)
	close(release)
	<-done
	if refused != enqueueFull {
		t.Fatalf("another row while the in-flight budget was full got %v, want enqueueFull", refused)
	}
	if a := s.Admission(); a.BufferedRows != 0 || a.PendingRows != 0 {
		t.Fatalf("admission after the insert = %+v", a)
	}
	if s.enqueue([]map[string]any{{"id": "d"}}, nil) != enqueueAccepted {
		t.Fatal("capacity not released after insert")
	}
}

func TestPendingBudgetWholeRequestRejectionAndConcurrency(t *testing.T) {
	s := New(Config{MaxPendingRows: 10}, nil, nil, nil, nil)
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.enqueue([]map[string]any{{"id": "x"}}, nil) }()
	}
	wg.Wait()
	if len(s.pend) != 10 || s.bufferedRows != 10 {
		t.Fatalf("budget exceeded: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
	}
	if r := s.Admission().Refusals; r.FullRequests != 90 || r.FullRows != 90 || r.OverBudgetAdmittedRequests != 0 {
		t.Fatalf("refusal counters = %+v, want 90 budget-full requests and rows", r)
	}
}

// Concurrent requests larger than the whole budget: at most one is let in while
// the buffer is below the budget, so the buffer exceeds the budget by at most
// one request; every other one is refused as retryable (budget full).
func TestOverBudgetRequestsOvershootTheBudgetByAtMostOneRequest(t *testing.T) {
	const budget = 10
	s := New(Config{MaxPendingRows: budget, BatchMaxAge: time.Hour}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	var wg sync.WaitGroup
	var mu sync.Mutex
	outcomes := map[enqueueOutcome]int{}
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got := s.enqueue(make([]map[string]any, budget+5), nil)
			mu.Lock()
			outcomes[got]++
			mu.Unlock()
		}()
	}
	wg.Wait()
	if outcomes[enqueueAccepted] != 1 || outcomes[enqueueFull] != 49 || s.bufferedRows != budget+5 || len(s.pend) != budget+5 {
		t.Fatalf("outcomes %v buffered %d pending %d; want exactly one over-budget request admitted", outcomes, s.bufferedRows, len(s.pend))
	}
	r := s.Admission().Refusals
	if r.OverBudgetAdmittedRequests != 1 || r.OverBudgetAdmittedRows != budget+5 || r.FullRequests != 49 || r.FullRows != 49*(budget+5) {
		t.Fatalf("refusal counters = %+v", r)
	}
}

func TestPendingBudgetRetryableHTTPAndGRPC(t *testing.T) {
	for _, protocol := range []string{"json", "protobuf", "grpc"} {
		t.Run(protocol, func(t *testing.T) {
			s := New(Config{MaxPendingRows: 1}, nil, nil, nil, nil)
			if s.enqueue([]map[string]any{{"id": "existing"}}, nil) != enqueueAccepted {
				t.Fatal("initial reservation failed")
			}
			traces := makeTraces("backpressure", "44444444-4444-4444-8444-444444444444")
			req := ptraceotlp.NewExportRequestFromTraces(traces)
			if protocol == "grpc" {
				_, err := (&otlpHandler{s: s}).Export(context.Background(), req)
				st, _ := status.FromError(err)
				if len(st.Details()) != 1 {
					t.Fatal("OTLP resource exhaustion must carry retry info")
				}
				if retry, ok := st.Details()[0].(*errdetails.RetryInfo); !ok || retry.RetryDelay.AsDuration() != time.Second {
					t.Fatal("missing retry delay")
				}
				if status.Code(err) != codes.ResourceExhausted {
					t.Fatalf("wrong grpc rejection: %v", err)
				}
			} else {
				body, _ := req.MarshalJSON()
				contentType := "application/json"
				if protocol == "protobuf" {
					body, _ = req.MarshalProto()
					contentType = "application/x-protobuf"
				}
				r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
				r.Header.Set("Content-Type", contentType)
				w := httptest.NewRecorder()
				s.handleHTTPTraces(w, r)
				if w.Code != http.StatusServiceUnavailable || w.Header().Get("Retry-After") != "1" {
					t.Fatalf("wrong HTTP rejection: %d %s", w.Code, w.Body.String())
				}
			}
			if s.bufferedRows != 1 || len(s.pend) != 1 || s.pendCurated != nil {
				t.Fatal("rejected request changed queue or sidecars")
			}
		})
	}
}

func partialTraces() ptrace.Traces {
	traces := ptrace.NewTraces()
	for i, name := range []string{"known", "unknown"} {
		rs := traces.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", name)
		spans := rs.ScopeSpans().AppendEmpty().Spans()
		for j := 0; j <= i*2; j++ {
			sp := spans.AppendEmpty()
			sp.SetName("partial-test")
			sp.SetSpanID([8]byte{byte(i + 1), byte(j + 1)})
			sp.SetTraceID([16]byte{byte(i + 1)})
		}
	}
	return traces
}

func TestProjectDropsReportSpanCountNotResourceCount(t *testing.T) {
	for _, protocol := range []string{"json", "protobuf", "grpc"} {
		t.Run(protocol, func(t *testing.T) {
			result := &auth.ResolveResult{OrgID: "11111111-1111-4111-8111-111111111111", WorkspaceID: "22222222-2222-4222-8222-222222222222", Projects: map[string]string{"known": "33333333-3333-4333-8333-333333333333"}}
			ctx := auth.WithResolvedContext(context.Background(), result, "fixture")
			s := New(Config{}, nil, nil, NoopUsageEmitter{}, NoopMetering{}, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
			req := ptraceotlp.NewExportRequestFromTraces(partialTraces())
			resp := ptraceotlp.NewExportResponse()
			if protocol == "grpc" {
				var err error
				resp, err = (&otlpHandler{s: s}).Export(ctx, req)
				if err != nil {
					t.Fatal(err)
				}
			} else {
				body, _ := req.MarshalJSON()
				contentType := "application/json"
				if protocol == "protobuf" {
					body, _ = req.MarshalProto()
					contentType = "application/x-protobuf"
				}
				r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body)).WithContext(ctx)
				r.Header.Set("Content-Type", contentType)
				w := httptest.NewRecorder()
				s.handleHTTPTraces(w, r)
				if w.Code != 200 {
					t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
				}
				var err error
				if protocol == "json" {
					err = resp.UnmarshalJSON(w.Body.Bytes())
				} else {
					err = resp.UnmarshalProto(w.Body.Bytes())
				}
				if err != nil {
					t.Fatal(err)
				}
			}
			if resp.PartialSuccess().RejectedSpans() != 3 || resp.PartialSuccess().ErrorMessage() == "" || len(s.pend) != 1 {
				t.Fatalf("lost drops: rejected=%d queued=%d", resp.PartialSuccess().RejectedSpans(), len(s.pend))
			}
		})
	}
}

// spansTraces builds one resource with n distinct spans, so a request carries
// exactly n converted rows.
func spansTraces(n int) ptrace.Traces {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "44444444-4444-4444-8444-444444444444")
	spans := rs.ScopeSpans().AppendEmpty().Spans()
	for i := 0; i < n; i++ {
		sp := spans.AppendEmpty()
		sp.SetName("budget")
		sp.SetTraceID([16]byte{0xaa})
		sp.SetSpanID([8]byte{0, 0, 0, 0, 0, 0, byte(i >> 8), byte(i)})
		sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
		sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(time.Millisecond)))
	}
	return traces
}

func exportHTTP(s *Server, req ptraceotlp.ExportRequest, protocol string) *httptest.ResponseRecorder {
	body, _ := req.MarshalJSON()
	contentType := "application/json"
	if protocol == "protobuf" {
		body, _ = req.MarshalProto()
		contentType = "application/x-protobuf"
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	r.Header.Set("Content-Type", contentType)
	w := httptest.NewRecorder()
	s.handleHTTPTraces(w, r)
	return w
}

// A request with more rows than the whole budget is never refused for good: an
// unbounded buffer used to accept it, and OTLP clients drop a non-retryable
// refusal. It is admitted while the buffer is below the budget, and while the
// buffer is over the budget every request is refused as retryable.
func TestOverBudgetRequestIsAdmittedBelowTheBudget(t *testing.T) {
	const budget = 4
	for _, protocol := range []string{"json", "protobuf", "grpc"} {
		t.Run(protocol, func(t *testing.T) {
			s := New(Config{MaxPendingRows: budget, BatchMaxAge: time.Hour}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
			export := func(n int) (ok bool, retryable bool, message string) {
				req := ptraceotlp.NewExportRequestFromTraces(spansTraces(n))
				if protocol == "grpc" {
					_, err := (&otlpHandler{s: s}).Export(context.Background(), req)
					if err == nil {
						return true, false, ""
					}
					st, _ := status.FromError(err)
					return false, otlpGRPCRetryable(err), st.Message()
				}
				w := exportHTTP(s, req, protocol)
				if w.Code == http.StatusOK {
					return true, false, ""
				}
				return false, otlpHTTPRetryable(w.Code) && w.Header().Get("Retry-After") == "1", w.Body.String()
			}
			if ok, _, msg := export(budget + 1); !ok {
				t.Fatalf("a request larger than the budget was refused by an empty buffer: %q", msg)
			}
			if s.bufferedRows != budget+1 || len(s.pend) != budget+1 {
				t.Fatalf("over-budget request parked %d rows reserving %d, want %d", len(s.pend), s.bufferedRows, budget+1)
			}
			// The buffer is over the budget now: any request waits for the drain.
			ok, retryable, msg := export(1)
			if ok || !retryable || !strings.Contains(msg, "retry with backoff") {
				t.Fatalf("request while over the budget: ok=%v retryable=%v %q; want a retryable refusal", ok, retryable, msg)
			}
			// Drained to exactly the budget (full): an over-budget request is
			// refused as retryable and the message tells the sender to split.
			s.pendMu.Lock()
			s.pend, s.bufferedRows = s.pend[:budget], budget
			s.pendMu.Unlock()
			ok, retryable, msg = export(budget + 1)
			if ok || !retryable || !strings.Contains(msg, "max_pending_rows (4)") || !strings.Contains(msg, "5 spans") {
				t.Fatalf("over-budget request into a full buffer: ok=%v retryable=%v %q", ok, retryable, msg)
			}
			if s.bufferedRows != budget || len(s.pend) != budget {
				t.Fatalf("refusals changed the queue: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
			}
			// One row free: the over-budget request is admitted again.
			s.pendMu.Lock()
			s.pend, s.bufferedRows = s.pend[:budget-1], budget-1
			s.pendMu.Unlock()
			if ok, _, msg := export(budget + 1); !ok {
				t.Fatalf("over-budget request with the buffer below the budget refused: %q", msg)
			}
			if r := s.Admission().Refusals; r.OverBudgetAdmittedRequests != 2 || r.OverBudgetAdmittedRows != 2*(budget+1) || r.FullRequests != 2 {
				t.Fatalf("refusal counters = %+v", r)
			}
		})
	}
}

// A request that exactly fills an empty buffer is admitted; one more row is a
// retryable refusal.
func TestExactFitAdmittedAndFullBufferRetryable(t *testing.T) {
	const budget = 4
	s := New(Config{MaxPendingRows: budget}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	if w := exportHTTP(s, ptraceotlp.NewExportRequestFromTraces(spansTraces(budget)), "json"); w.Code != http.StatusOK {
		t.Fatalf("a request that exactly fits an empty buffer got %d: %s", w.Code, w.Body.String())
	}
	full := exportHTTP(s, ptraceotlp.NewExportRequestFromTraces(spansTraces(1)), "json")
	if full.Code != http.StatusServiceUnavailable || full.Header().Get("Retry-After") != "1" {
		t.Fatalf("a request over a full buffer got %d (Retry-After %q), want 503 with Retry-After", full.Code, full.Header().Get("Retry-After"))
	}
	if s.bufferedRows != budget || len(s.pend) != budget {
		t.Fatalf("refusals changed the queue: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
	}
}

// A flush size above the budget could never trigger (parked rows stay within
// the budget), leaving only the age timer to drain a full buffer. It is clamped
// to the budget, so filling the budget wakes the flusher at once.
func TestBatchSizeAboveTheBudgetIsClampedSoAFullBufferFlushes(t *testing.T) {
	var logs bytes.Buffer
	s := New(Config{MaxPendingRows: 3, BatchMaxRows: 5000, BatchMaxAge: time.Hour}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(&logs, nil))))
	if l := s.Limits(); l.BatchMaxRows != 3 || l.MaxPendingRows != 3 {
		t.Fatalf("limits = %+v, want the flush size clamped to the budget", l)
	}
	if !strings.Contains(logs.String(), "batch_max_rows is above max_pending_rows") {
		t.Fatalf("the clamp was not logged: %q", logs.String())
	}
	if s.enqueue(spansRows(3), nil) != enqueueAccepted {
		t.Fatal("rows within the budget refused")
	}
	select {
	case <-s.pendCh:
	default:
		t.Fatal("a buffer filled to the budget did not wake the flusher")
	}
	// A consistent configuration is left alone.
	if l := New(Config{MaxPendingRows: 20000, BatchMaxRows: 5000}, nil, nil, nil, nil).Limits(); l.BatchMaxRows != 5000 {
		t.Fatalf("limits = %+v", l)
	}
}

// A handler that outlives the HTTP shutdown timeout must not acknowledge rows
// after the final drain: nothing would ever flush them. Once admission is
// closed every request is refused as retryable and nothing is parked.
func TestRequestsAfterTheFinalDrainAreRefusedAsRetryable(t *testing.T) {
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = io.Copy(io.Discard, r.Body) }))
	defer stub.Close()
	writer, err := chwriter.New(chwriter.Config{URL: stub.URL, Database: "default", Table: "spans", MaxRetries: 1, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := New(Config{GRPCAddr: testTCPAddr(t), HTTPAddr: testTCPAddr(t), MaxPendingRows: 100, BatchMaxAge: time.Hour}, writer, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Run(ctx); close(done) }()
	if !waitPort(s.cfg.HTTPAddr, 2*time.Second) {
		cancel()
		t.Fatal("server did not start")
	}
	cancel()
	<-done
	// What a straggling handler does after Run returned.
	for _, protocol := range []string{"json", "grpc"} {
		req := ptraceotlp.NewExportRequestFromTraces(spansTraces(2))
		if protocol == "grpc" {
			_, err := (&otlpHandler{s: s}).Export(context.Background(), req)
			if status.Code(err) != codes.Unavailable || !otlpGRPCRetryable(err) {
				t.Fatalf("gRPC export after the final drain: %v, want retryable UNAVAILABLE", err)
			}
		} else if w := exportHTTP(s, req, protocol); w.Code != http.StatusServiceUnavailable || w.Header().Get("Retry-After") == "" {
			t.Fatalf("HTTP export after the final drain: %d (Retry-After %q), want 503 with Retry-After", w.Code, w.Header().Get("Retry-After"))
		}
	}
	if a := s.Admission(); a.BufferedRows != 0 || a.PendingRows != 0 || a.Refusals.ShuttingDownRequests != 2 {
		t.Fatalf("admission after shutdown = %+v; rows parked after the final drain would never be flushed", a)
	}
}

// A routed request that is split into a hidden and a visible parcel must be
// admitted or refused as one unit. If the first parcel were kept when the second
// does not fit, the client's retry of the refused request would store it twice.
func TestSplitRoutedRequestIsAdmittedAtomically(t *testing.T) {
	row := func(project string) map[string]any { return map[string]any{"project_id": project} }
	hiddenProject := "99999999-9999-4999-8999-999999999999"
	visibleProject := "55555555-5555-4555-8555-555555555555"
	hiddenSet := map[string]struct{}{hiddenProject: {}}
	visibleSet := map[string]struct{}{visibleProject: {}}
	rows := []map[string]any{row(hiddenProject), row(hiddenProject), row(visibleProject), row(visibleProject)}

	s := New(Config{MaxPendingRows: 5}, nil, nil, nil, nil)
	if got := s.enqueue([]map[string]any{{"id": "existing-1"}, {"id": "existing-2"}}, nil); got != enqueueAccepted {
		t.Fatalf("setup: %v", got)
	}
	// 2 buffered + 4 rows = 6 > 5. The hidden parcel alone (2) would fit.
	got := s.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", visibleSet, hiddenSet)
	if got != enqueueFull {
		t.Fatalf("split request over the budget got %v, want enqueueFull", got)
	}
	if len(s.pend) != 2 || s.bufferedRows != 2 {
		t.Fatalf("a refused split request left a parcel behind: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
	}
	// A retry once the budget is free stores each row exactly once.
	s.pendMu.Lock()
	s.pend, s.bufferedRows = nil, 0
	s.pendMu.Unlock()
	if got := s.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", visibleSet, hiddenSet); got != enqueueAccepted {
		t.Fatalf("retry got %v, want enqueueAccepted", got)
	}
	if len(s.pend) != 4 || s.bufferedRows != 4 {
		t.Fatalf("retry stored %d rows reserving %d, want 4/4", len(s.pend), s.bufferedRows)
	}
	// A split request larger than the whole budget is also one unit: refused
	// (retryable, nothing parked) by a full buffer, and admitted with both
	// parcels by a buffer below the budget.
	s2 := New(Config{MaxPendingRows: 3}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	if got := s2.enqueue(spansRows(3), nil); got != enqueueAccepted {
		t.Fatalf("setup: %v", got)
	}
	if got := s2.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", visibleSet, hiddenSet); got != enqueueFull {
		t.Fatalf("split request larger than the budget into a full buffer got %v, want enqueueFull", got)
	}
	if len(s2.pend) != 3 || s2.bufferedRows != 3 {
		t.Fatal("refused over-budget split request left a parcel behind")
	}
	s2.pendMu.Lock()
	s2.pend, s2.bufferedRows = nil, 0
	s2.pendMu.Unlock()
	if got := s2.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", visibleSet, hiddenSet); got != enqueueAccepted {
		t.Fatalf("split request larger than the budget into an empty buffer got %v, want enqueueAccepted", got)
	}
	if len(s2.pend) != 4 || s2.bufferedRows != 4 {
		t.Fatalf("over-budget split request stored %d rows reserving %d, want 4/4", len(s2.pend), s2.bufferedRows)
	}
}
