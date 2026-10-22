package server

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
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
