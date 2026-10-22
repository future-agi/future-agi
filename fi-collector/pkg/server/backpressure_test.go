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
	done := make(chan struct{})
	go func() { s.drainNow(context.Background()); close(done) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		close(release)
		t.Fatal("writer never blocked")
	}
	// The old code swaps pend to nil before the insert. A cap on len(pend)
	// alone would let more rows in and is not a bounded in-flight budget.
	refused := s.enqueue([]map[string]any{{"id": "c"}}, nil)
	close(release)
	<-done
	if refused != enqueueFull {
		t.Fatalf("another row while the in-flight budget was full got %v, want enqueueFull", refused)
	}
	if s.enqueue([]map[string]any{{"id": "d"}}, nil) != enqueueAccepted {
		t.Fatal("capacity not released after insert")
	}
}

func TestPendingBudgetWholeRequestRejectionAndConcurrency(t *testing.T) {
	s := New(Config{MaxPendingRows: 10}, nil, nil, nil, nil)
	if got := s.enqueue(make([]map[string]any, 11), nil); got != enqueueOversize {
		t.Fatalf("request over the whole budget got %v, want enqueueOversize", got)
	}
	if len(s.pend) != 0 || s.bufferedRows != 0 {
		t.Fatalf("oversized request left rows behind: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
	}
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.enqueue([]map[string]any{{"id": "x"}}, nil) }()
	}
	wg.Wait()
	if len(s.pend) != 10 || s.bufferedRows != 10 {
		t.Fatalf("budget exceeded: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
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

// A request with more rows than the whole budget can never be admitted, so it
// must not be answered with a status that tells the client to retry it.
func TestOversizedRequestIsNotRetryable(t *testing.T) {
	const budget = 4
	for _, protocol := range []string{"json", "protobuf", "grpc"} {
		t.Run(protocol, func(t *testing.T) {
			s := New(Config{MaxPendingRows: budget}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
			req := ptraceotlp.NewExportRequestFromTraces(spansTraces(budget + 1))
			if protocol == "grpc" {
				_, err := (&otlpHandler{s: s}).Export(context.Background(), req)
				st, _ := status.FromError(err)
				if st.Code() != codes.InvalidArgument {
					t.Fatalf("oversized gRPC request got %v, want InvalidArgument", err)
				}
				if len(st.Details()) != 0 {
					t.Fatal("a non-retryable refusal must not carry RetryInfo")
				}
				if !strings.Contains(st.Message(), "max_pending_rows (4)") || !strings.Contains(st.Message(), "5 spans") {
					t.Fatalf("message does not name the request size and the limit: %q", st.Message())
				}
			} else {
				w := exportHTTP(s, req, protocol)
				if w.Code != http.StatusRequestEntityTooLarge {
					t.Fatalf("oversized HTTP request got %d, want 413", w.Code)
				}
				if w.Header().Get("Retry-After") != "" {
					t.Fatal("a non-retryable refusal must not carry Retry-After")
				}
				if !strings.Contains(w.Body.String(), "max_pending_rows (4)") {
					t.Fatalf("body does not name the limit: %q", w.Body.String())
				}
			}
			if len(s.pend) != 0 || s.bufferedRows != 0 {
				t.Fatalf("oversized request changed the queue: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
			}
		})
	}
}

// The status of a request over a full buffer and of one that can never fit must
// differ, and a request that exactly fills an empty buffer is admitted.
func TestFullBufferRetryableOversizeNotAndExactFitAdmitted(t *testing.T) {
	const budget = 4
	s := New(Config{MaxPendingRows: budget}, nil, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
	if w := exportHTTP(s, ptraceotlp.NewExportRequestFromTraces(spansTraces(budget)), "json"); w.Code != http.StatusOK {
		t.Fatalf("a request that exactly fits an empty buffer got %d: %s", w.Code, w.Body.String())
	}
	full := exportHTTP(s, ptraceotlp.NewExportRequestFromTraces(spansTraces(1)), "json")
	if full.Code != http.StatusServiceUnavailable || full.Header().Get("Retry-After") != "1" {
		t.Fatalf("a request over a full buffer got %d (Retry-After %q), want 503 with Retry-After", full.Code, full.Header().Get("Retry-After"))
	}
	oversize := exportHTTP(s, ptraceotlp.NewExportRequestFromTraces(spansTraces(budget+1)), "json")
	if oversize.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversize request got %d while the buffer was full, want 413 (never a retryable status)", oversize.Code)
	}
	if s.bufferedRows != budget || len(s.pend) != budget {
		t.Fatalf("refusals changed the queue: pending=%d reserved=%d", len(s.pend), s.bufferedRows)
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
	// The whole request over the entire budget is not retryable even though
	// each parcel alone would fit.
	s2 := New(Config{MaxPendingRows: 3}, nil, nil, nil, nil)
	if got := s2.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", visibleSet, hiddenSet); got != enqueueOversize {
		t.Fatalf("split request larger than the budget got %v, want enqueueOversize", got)
	}
	if len(s2.pend) != 0 || s2.bufferedRows != 0 {
		t.Fatal("oversized split request left rows behind")
	}
}
