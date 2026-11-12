package server

import (
	"bytes"
	"context"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// Row backlog instrumentation must not replace dev's byte admission. A stalled
// insert remains visible as backlog, and its real work lease stays owned.
func TestBacklogAndByteLeaseIncludeBlockedInflightInsert(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		once.Do(func() { close(entered) })
		<-release
	}))
	defer ch.Close()
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	defer unblock()
	writer, err := chwriter.New(chwriter.Config{URL: ch.URL, MaxRetries: 1, RequestTimeout: 5 * time.Second, DeadLetterFile: t.TempDir() + "/dl"})
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	s := New(Config{}, writer, nil, nil, nil)
	held, err := s.work.acquire(context.Background(), 1024)
	if err != nil {
		t.Fatal(err)
	}
	s.enqueue(spansRows(2), nil)
	s.adoptRows(held)
	if a := s.Admission(); a.BufferedRows != 2 || a.PendingRows != 2 {
		t.Fatalf("parked: %+v", a)
	}
	done := make(chan struct{})
	go func() { s.drainNow(context.Background()); close(done) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("insert did not start")
	}
	if a := s.Admission(); a.BufferedRows != 2 || a.PendingRows != 0 {
		t.Fatalf("in flight: %+v", a)
	}
	if used := s.work.inUse.Load(); used != 1024 {
		t.Fatalf("lease released early: %d", used)
	}
	// Let the insert and synchronous sidecars finish before checking ownership.
	// Keep the lease until the blocked writer has finished.
	unblock()
	<-done
	if a := s.Admission(); a.BufferedRows != 0 || a.PendingRows != 0 {
		t.Fatalf("drained: %+v", a)
	}
	if used := s.work.inUse.Load(); used != 0 {
		t.Fatalf("lease leaked: %d", used)
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
	s := New(Config{GRPCAddr: testTCPAddr(t), HTTPAddr: testTCPAddr(t), BatchMaxAge: time.Hour}, writer, nil, nil, nil, WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))
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

// Hidden and visible parcels remain all-or-nothing at the shutdown fence. Their
// sidecars must keep dev's exact input positions, not duplicate span IDs.
func TestSplitRoutedRequestShutdownAtomicityAndSidecarPositions(t *testing.T) {
	s := New(Config{}, nil, nil, nil, nil)
	s.propertyCatalog = nopCatalog{}
	rows := []map[string]any{{"project_id": "hidden"}, {"project_id": "visible"}, {"project_id": "hidden"}, {"project_id": "visible"}}
	got := s.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", map[string]struct{}{"visible": {}}, map[string]struct{}{"hidden": {}})
	if got != enqueueAccepted || len(s.pend) != 4 || len(s.pendPropertyRows) != 2 || s.pendPropertyRows[0] != 2 || s.pendPropertyRows[1] != 3 {
		t.Fatalf("parcel indices: %v %+v", got, s.pendPropertyRows)
	}
	s.closeAdmission()
	if got := s.enqueueRouted(rows, nil, auth.RouteOutcome{}, "org", "ws", nil, map[string]struct{}{"hidden": {}}); got != enqueueClosed || len(s.pend) != 4 {
		t.Fatalf("shutdown kept a parcel: %v %d", got, len(s.pend))
	}
}
