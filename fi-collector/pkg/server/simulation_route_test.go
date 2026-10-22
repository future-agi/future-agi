package server

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

const (
	routeOrg        = "11111111-1111-4111-8111-111111111111"
	routeWorkspace  = "22222222-2222-4222-8222-222222222222"
	routeAgent      = "33333333-3333-4333-8333-333333333333"
	routeHiddenID   = "44444444-4444-4444-8444-444444444444"
	routeProdID     = "55555555-5555-4555-8555-555555555555"
	routeHiddenName = "fi-simulation-" + routeAgent
)

// routeFake stands in for the authenticator's routing: a sandbox key's request
// is renamed into the hidden project, or dropped whole for dropReason, or the
// router fails with err. Any other key is never touched.
type routeFake struct {
	mu         sync.Mutex
	calls      int
	err        error
	dropReason string
}

func (f *routeFake) RouteSimulation(_ context.Context, _ string, traces ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
	f.mu.Lock()
	f.calls++
	f.mu.Unlock()
	if f.err != nil {
		return auth.RouteOutcome{}, f.err
	}
	if result.KeyType != "ingest" {
		return auth.RouteOutcome{}, nil
	}
	if f.dropReason != "" {
		spans := traces.SpanCount()
		traces.ResourceSpans().RemoveIf(func(ptrace.ResourceSpans) bool { return true })
		return auth.RouteOutcome{Dropped: map[string]int{f.dropReason: spans}}, nil
	}
	rss := traces.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		rss.At(i).Resource().Attributes().PutStr("project_name", routeHiddenName)
		rss.At(i).Resource().Attributes().PutStr("fi.simulation.origin", "simulation")
	}
	return auth.RouteOutcome{Hidden: routeHiddenID}, nil
}

func (f *routeFake) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

type routeUsageCall struct {
	org           string
	traces, spans int
	payloadBytes  int64
}

type routeUsage struct {
	mu    sync.Mutex
	calls []routeUsageCall
}

func (u *routeUsage) EmitIngestion(orgID string, numTraces, numSpans int, payloadBytes int64, _ string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.calls = append(u.calls, routeUsageCall{orgID, numTraces, numSpans, payloadBytes})
}

func (u *routeUsage) snapshot() []routeUsageCall {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]routeUsageCall(nil), u.calls...)
}

type routeNotifier struct {
	mu    sync.Mutex
	calls int
}

func (n *routeNotifier) EnqueueRoots([]traceavailable.Root) error {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.calls++
	return nil
}

// routeServer is a server on a ClickHouse stub that accepts every insert.
type routeServer struct {
	*Server
	events *routeUsage
	fake   *routeFake
	logs   *bytes.Buffer
}

func newRouteServer(t *testing.T, router *routeFake, opts ...Option) *routeServer {
	t.Helper()
	s := newServerOn(t, nil, append([]Option{WithSimulationRouter(router)}, opts...)...)
	s.fake = router
	return s
}

// newServerOn is a server on a ClickHouse stub, built through New with
// authenticator and opts as a caller would.
func newServerOn(t *testing.T, authenticator *auth.Authenticator, opts ...Option) *routeServer {
	t.Helper()
	ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }))
	t.Cleanup(ch.Close)
	writer := newSpanTestWriter(t, ch.URL, t.TempDir()+"/dl.jsonl")
	t.Cleanup(func() { _ = writer.Close() })
	usage := &routeUsage{}
	logs := &bytes.Buffer{}
	all := append([]Option{WithLogger(slog.New(slog.NewJSONHandler(logs, nil)))}, opts...)
	return &routeServer{
		Server: New(Config{}, writer, authenticator, usage, NoopMetering{}, all...),
		events: usage, logs: logs,
	}
}

func sandboxKey() *auth.ResolveResult {
	return &auth.ResolveResult{
		OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "ingest",
		Projects: map[string]string{routeHiddenName: routeHiddenID, "customer-prod": routeProdID},
	}
}

func systemKey() *auth.ResolveResult {
	return &auth.ResolveResult{
		OrgID: routeOrg, WorkspaceID: routeWorkspace, KeyType: "system",
		Projects: map[string]string{routeHiddenName: routeHiddenID, "customer-prod": routeProdID},
	}
}

// routeRequest is one resource per name, each with two spans of one trace.
func routeRequest(names ...string) ptraceotlp.ExportRequest {
	traces := ptrace.NewTraces()
	for i, name := range names {
		rs := traces.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", name)
		spans := rs.ScopeSpans().AppendEmpty().Spans()
		for j := 0; j < 2; j++ {
			sp := spans.AppendEmpty()
			sp.SetName("routed-span")
			sp.SetTraceID(pcommon.TraceID([16]byte{0xaa, 0xbb}))
			sp.SetSpanID(pcommon.SpanID([8]byte{byte(i) + 1, byte(j) + 1}))
			sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
			sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(time.Millisecond)))
		}
	}
	return ptraceotlp.NewExportRequestFromTraces(traces)
}

func (s *routeServer) export(result *auth.ResolveResult, req ptraceotlp.ExportRequest) (ptraceotlp.ExportResponse, error) {
	ctx := auth.WithResolvedContext(context.Background(), result, "cache-key")
	return (&otlpHandler{s: s.Server}).Export(ctx, req)
}

func (s *routeServer) post(t *testing.T, result *auth.ResolveResult, req ptraceotlp.ExportRequest, contentType string) *httptest.ResponseRecorder {
	t.Helper()
	var body []byte
	var err error
	if contentType == "application/json" {
		body, err = req.MarshalJSON()
	} else {
		body, err = req.MarshalProto()
	}
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	r.Header.Set("Content-Type", contentType)
	r = r.WithContext(auth.WithResolvedContext(r.Context(), result, "cache-key"))
	rec := httptest.NewRecorder()
	s.handleHTTPTraces(rec, r)
	return rec
}

// pending is the number of rows, property rows and roots parked for the flush.
func (s *routeServer) pending() (rows, property, roots int) {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	return len(s.pend), len(s.pendProperty), len(s.pendRoots)
}

func (s *routeServer) pendingProjects() map[string]int {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	out := map[string]int{}
	for _, row := range s.pend {
		id, _ := row["project_id"].(string)
		out[id]++
	}
	return out
}

func TestHiddenProjectInsertErrorRefusesTheRequestAndStoresNothing(t *testing.T) {
	router := &routeFake{err: errors.New("the hidden project could not be made")}
	s := newRouteServer(t, router)

	_, err := s.export(sandboxKey(), routeRequest("customer-prod"))
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("gRPC code = %v (%v), want Unavailable", status.Code(err), err)
	}

	for _, contentType := range []string{"application/x-protobuf", "application/json"} {
		rec := s.post(t, sandboxKey(), routeRequest("customer-prod"), contentType)
		if rec.Code != http.StatusServiceUnavailable || rec.Header().Get("Retry-After") != "5" {
			t.Fatalf("HTTP %s: status %d, Retry-After %q", contentType, rec.Code, rec.Header().Get("Retry-After"))
		}
	}

	if rows, property, roots := s.pending(); rows+property+roots != 0 {
		t.Fatalf("stored %d rows, %d property rows, %d roots", rows, property, roots)
	}
	if calls := s.events.snapshot(); len(calls) != 0 {
		t.Fatalf("a refused request was metered: %+v", calls)
	}
	if router.callCount() != 3 {
		t.Fatalf("router called %d times", router.callCount())
	}
}

func TestPartialSuccessCountsRejectedSpans(t *testing.T) {
	router := &routeFake{dropReason: "no_agent"}
	s := newRouteServer(t, router)
	newReq := func() ptraceotlp.ExportRequest { return routeRequest("customer-prod", "other") }
	spans := newReq().Traces().SpanCount()

	t.Run("gRPC", func(t *testing.T) {
		resp, err := s.export(sandboxKey(), newReq())
		if err != nil {
			t.Fatal(err)
		}
		if got := resp.PartialSuccess().RejectedSpans(); got != int64(spans) {
			t.Fatalf("rejected = %d, want %d", got, spans)
		}
		if msg := resp.PartialSuccess().ErrorMessage(); !strings.Contains(msg, "no_agent=4") {
			t.Fatalf("message = %q", msg)
		}
	})
	for _, contentType := range []string{"application/x-protobuf", "application/json"} {
		t.Run("HTTP "+contentType, func(t *testing.T) {
			rec := s.post(t, sandboxKey(), newReq(), contentType)
			if rec.Code != http.StatusOK || rec.Header().Get("Content-Type") != contentType {
				t.Fatalf("status %d, content type %q", rec.Code, rec.Header().Get("Content-Type"))
			}
			resp := ptraceotlp.NewExportResponse()
			var err error
			if contentType == "application/json" {
				err = resp.UnmarshalJSON(rec.Body.Bytes())
			} else {
				err = resp.UnmarshalProto(rec.Body.Bytes())
			}
			if err != nil {
				t.Fatal(err)
			}
			if got := resp.PartialSuccess().RejectedSpans(); got != int64(spans) {
				t.Fatalf("rejected = %d, want %d", got, spans)
			}
			if msg := resp.PartialSuccess().ErrorMessage(); !strings.Contains(msg, "no_agent=4") {
				t.Fatalf("message = %q", msg)
			}
		})
	}

	// Nothing of a dropped request is stored, and none of it is billed, whatever
	// the billing mode: the request returns before metering.
	if rows, _, _ := s.pending(); rows != 0 {
		t.Fatalf("%d rows stored", rows)
	}
	if calls := s.events.snapshot(); len(calls) != 0 {
		t.Fatalf("a dropped request was metered: %+v", calls)
	}

	t.Run("a routed request has no partial success", func(t *testing.T) {
		resp, err := s.export(systemKey(), routeRequest("customer-prod"))
		if err != nil {
			t.Fatal(err)
		}
		if resp.PartialSuccess().RejectedSpans() != 0 || resp.PartialSuccess().ErrorMessage() != "" {
			t.Fatalf("partial success on a full success: %d %q",
				resp.PartialSuccess().RejectedSpans(), resp.PartialSuccess().ErrorMessage())
		}
	})
}

func TestHiddenRowsGoToNeitherCatalogNorNotifier(t *testing.T) {
	catalog := &propertyCatalogWriterStub{}
	notifier := &routeNotifier{}
	router := &routeFake{}
	s := newRouteServer(t, router, WithPropertyCatalogWriter(catalog), WithTraceNotifier(notifier))

	for _, send := range []func() error{
		func() error { _, err := s.export(sandboxKey(), routeRequest("customer-prod", "other")); return err },
		func() error {
			if rec := s.post(t, sandboxKey(), routeRequest("customer-prod"), "application/json"); rec.Code != http.StatusOK {
				return errors.New(rec.Body.String())
			}
			return nil
		},
	} {
		if err := send(); err != nil {
			t.Fatal(err)
		}
	}

	rows, property, roots := s.pending()
	if rows == 0 {
		t.Fatal("the routed spans were not stored")
	}
	if property != 0 || roots != 0 {
		t.Fatalf("hidden rows parked for the catalog (%d) or the notifier (%d)", property, roots)
	}
	if projects := s.pendingProjects(); len(projects) != 1 || projects[routeHiddenID] != rows {
		t.Fatalf("rows by project = %v, want all in the hidden project", projects)
	}

	s.drainNow(context.Background())
	if catalog.calls != 0 || notifier.calls != 0 {
		t.Fatalf("catalog called %d times, notifier %d", catalog.calls, notifier.calls)
	}
	if s.writer.Snapshot().BatchesInserted != 1 {
		t.Fatalf("writer stats = %+v", s.writer.Snapshot())
	}
	// A routed request must not look like a catalog failure to an operator.
	if strings.Contains(s.logs.String(), "observed_catalog") || strings.Contains(s.logs.String(), "notification gap") {
		t.Fatalf("logs = %s", s.logs.String())
	}

	// The same server hands a request of another key to the catalog.
	if _, err := s.export(systemKey(), routeRequest("customer-prod")); err != nil {
		t.Fatal(err)
	}
	if _, property, _ := s.pending(); property == 0 {
		t.Fatal("a request of another key was not handed to the catalog")
	}
}

func TestAuthenticatorIsTheDefaultRouterOfSandboxKeys(t *testing.T) {
	// No router option: the authenticator routes. A zero authenticator has not
	// checked its columns, so it refuses a sandbox-key request as retryable;
	// a server with no router would drop the spans instead.
	s := newServerOn(t, &auth.Authenticator{})
	if s.router == nil {
		t.Fatal("a server built with an authenticator has no router")
	}

	if _, err := s.export(sandboxKey(), routeRequest("customer-prod")); status.Code(err) != codes.Unavailable {
		t.Fatalf("gRPC: err = %v, want Unavailable from the authenticator", err)
	}
	rec := s.post(t, sandboxKey(), routeRequest("customer-prod"), "application/x-protobuf")
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("HTTP status = %d, want 503 from the authenticator", rec.Code)
	}
	if strings.Contains(s.logs.String(), dropNoRouter) {
		t.Fatalf("the request was dropped for want of a router: %s", s.logs.String())
	}

	// An option still replaces the default.
	fake := &routeFake{}
	replaced := newServerOn(t, &auth.Authenticator{}, WithSimulationRouter(fake))
	if _, err := replaced.export(sandboxKey(), routeRequest("customer-prod")); err != nil {
		t.Fatal(err)
	}
	if fake.callCount() != 1 {
		t.Fatalf("the replacement router was called %d times", fake.callCount())
	}
}

func TestHiddenProjectNamedByAnotherKeyReachesNeitherCatalogNorNotifier(t *testing.T) {
	// A key that is not a sandbox key may name a hidden project: the rows are
	// stored, but the project is not the key's to feed to the catalog or the
	// notifier. The rest of the same request still is.
	catalog := &propertyCatalogWriterStub{}
	notifier := &routeNotifier{}
	s := newRouteServer(t, &routeFake{}, WithPropertyCatalogWriter(catalog), WithTraceNotifier(notifier))

	for name, send := range map[string]func(req ptraceotlp.ExportRequest) error{
		"gRPC": func(req ptraceotlp.ExportRequest) error { _, err := s.export(systemKey(), req); return err },
		"HTTP": func(req ptraceotlp.ExportRequest) error {
			if rec := s.post(t, systemKey(), req, "application/x-protobuf"); rec.Code != http.StatusOK {
				return errors.New(rec.Body.String())
			}
			return nil
		},
	} {
		t.Run(name, func(t *testing.T) {
			s.pendMu.Lock()
			s.pend, s.pendProperty, s.pendRoots = nil, nil, nil
			s.pendMu.Unlock()

			if err := send(routeRequest(routeHiddenName)); err != nil {
				t.Fatal(err)
			}
			rows, property, roots := s.pending()
			if rows != 2 || property != 0 || roots != 0 {
				t.Fatalf("hidden request: %d rows, %d property rows, %d roots; want 2 stored and none handed on", rows, property, roots)
			}

			if err := send(routeRequest(routeHiddenName, "customer-prod")); err != nil {
				t.Fatal(err)
			}
			s.pendMu.Lock()
			defer s.pendMu.Unlock()
			if len(s.pend) != 6 {
				t.Fatalf("%d rows stored, want all 6", len(s.pend))
			}
			if len(s.pendProperty) != 2 {
				t.Fatalf("%d property rows, want the 2 of the visible project", len(s.pendProperty))
			}
			for _, span := range s.pendProperty {
				if span.Row["project_id"] != routeProdID {
					t.Errorf("property row of project %v", span.Row["project_id"])
				}
			}
			for _, root := range s.pendRoots {
				if root.ProjectID == routeHiddenID {
					t.Errorf("root of the hidden project: %+v", root)
				}
			}
		})
	}

	s.drainNow(context.Background())
	for _, span := range catalog.rows {
		if span.Row["project_id"] == routeHiddenID {
			t.Fatalf("the catalog was handed a row of the hidden project")
		}
	}
	if strings.Contains(s.logs.String(), "observed_catalog") {
		t.Fatalf("logs = %s", s.logs.String())
	}
}

func TestMissingColumnDropsSandboxKeyRequestsOnly(t *testing.T) {
	router := &routeFake{dropReason: "column_missing"}
	s := newRouteServer(t, router)

	resp, err := s.export(sandboxKey(), routeRequest("customer-prod"))
	if err != nil {
		t.Fatal(err)
	}
	if resp.PartialSuccess().RejectedSpans() != 2 || !strings.Contains(resp.PartialSuccess().ErrorMessage(), "column_missing=2") {
		t.Fatalf("partial success = %d %q", resp.PartialSuccess().RejectedSpans(), resp.PartialSuccess().ErrorMessage())
	}
	if rows, _, _ := s.pending(); rows != 0 {
		t.Fatalf("a dropped request stored %d rows", rows)
	}

	// Every other key is served as before, in its own project.
	if _, err := s.export(systemKey(), routeRequest("customer-prod")); err != nil {
		t.Fatal(err)
	}
	if rec := s.post(t, systemKey(), routeRequest("customer-prod"), "application/x-protobuf"); rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if projects := s.pendingProjects(); len(projects) != 1 || projects[routeProdID] != 4 {
		t.Fatalf("rows by project = %v", projects)
	}
}

func TestUsageCountsRoutedSpansOnce(t *testing.T) {
	s := newRouteServer(t, &routeFake{})
	spans := routeRequest("customer-prod", "other").Traces().SpanCount()

	if _, err := s.export(sandboxKey(), routeRequest("customer-prod", "other")); err != nil {
		t.Fatal(err)
	}
	if rec := s.post(t, sandboxKey(), routeRequest("customer-prod", "other"), "application/json"); rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	calls := s.events.snapshot()
	if len(calls) != 2 {
		t.Fatalf("%d usage events for 2 requests", len(calls))
	}
	for i, call := range calls {
		if call.org != routeOrg || call.spans != spans || call.traces != 1 || call.payloadBytes <= 0 {
			t.Errorf("event %d = %+v, want org %s, %d spans, 1 trace", i, call, routeOrg, spans)
		}
	}
	if rows, _, _ := s.pending(); rows != 2*spans {
		t.Fatalf("%d rows stored for 2 requests of %d spans", rows, spans)
	}
}

func TestServerWithoutARouterDropsSandboxKeysAndServesOthers(t *testing.T) {
	ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }))
	defer ch.Close()
	writer := newSpanTestWriter(t, ch.URL, t.TempDir()+"/dl.jsonl")
	defer writer.Close()
	usage := &routeUsage{}
	logs := &bytes.Buffer{}
	s := &routeServer{
		Server: New(Config{}, writer, nil, usage, NoopMetering{}, WithLogger(slog.New(slog.NewJSONHandler(logs, nil)))),
		events: usage, logs: logs,
	}

	// A sandbox key's request is dropped, never stored under its own name.
	resp, err := s.export(sandboxKey(), routeRequest("customer-prod"))
	if err != nil {
		t.Fatal(err)
	}
	if resp.PartialSuccess().RejectedSpans() != 2 || !strings.Contains(resp.PartialSuccess().ErrorMessage(), "no_router=2") {
		t.Fatalf("partial success = %d %q", resp.PartialSuccess().RejectedSpans(), resp.PartialSuccess().ErrorMessage())
	}
	rec := s.post(t, sandboxKey(), routeRequest("customer-prod"), "application/x-protobuf")
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if rows, _, _ := s.pending(); rows != 0 {
		t.Fatalf("%d rows of a sandbox key stored without a router", rows)
	}
	if calls := s.events.snapshot(); len(calls) != 0 {
		t.Fatalf("a dropped request was metered: %+v", calls)
	}
	// Two drops inside a minute log one line.
	if n := strings.Count(logs.String(), `"reason":"no_router"`); n != 1 {
		t.Fatalf("%d log lines for two drops: %s", n, logs.String())
	}

	// Any other key is served where its own name says.
	if _, err := s.export(systemKey(), routeRequest("customer-prod")); err != nil {
		t.Fatal(err)
	}
	if projects := s.pendingProjects(); len(projects) != 1 || projects[routeProdID] != 2 {
		t.Fatalf("rows by project = %v", projects)
	}
}

func TestRefusalLogIsQuietWhenNotReadyAndLimitedOtherwise(t *testing.T) {
	t.Run("not ready is not a warning", func(t *testing.T) {
		router := &routeFake{err: auth.ErrRoutingNotReady}
		s := newRouteServer(t, router)
		for i := 0; i < 3; i++ {
			if _, err := s.export(sandboxKey(), routeRequest("customer-prod")); status.Code(err) != codes.Unavailable {
				t.Fatalf("gRPC code = %v", status.Code(err))
			}
		}
		if s.logs.Len() != 0 {
			t.Fatalf("logged at the default level: %s", s.logs.String())
		}
	})
	t.Run("other refusals log once a minute and say no project was attempted", func(t *testing.T) {
		router := &routeFake{err: errors.New("connection reset")}
		s := newRouteServer(t, router)
		for i := 0; i < 3; i++ {
			if _, err := s.export(sandboxKey(), routeRequest("customer-prod")); status.Code(err) != codes.Unavailable {
				t.Fatalf("gRPC code = %v", status.Code(err))
			}
		}
		if n := strings.Count(s.logs.String(), `"level":"WARN"`); n != 1 {
			t.Fatalf("%d warnings for 3 refusals: %s", n, s.logs.String())
		}
		if strings.Contains(s.logs.String(), "could not make") {
			t.Fatalf("the line claims a project was attempted: %s", s.logs.String())
		}
	})
}
