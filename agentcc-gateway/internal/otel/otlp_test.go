package otel

import (
	"encoding/hex"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// collector is a fake OTLP receiver that decodes request bodies with the same
// generated protos the exporter encodes with.
type collector struct {
	*httptest.Server

	mu       sync.Mutex
	batches  [][]*tracepb.Span
	resource []*commonpb.KeyValue
	paths    []string
	ctypes   []string
	headers  []http.Header

	status   func(n int) int // per-request status code, n is 1-based
	received chan struct{}
}

func newCollector(t *testing.T, status func(n int) int) *collector {
	t.Helper()
	c := &collector{status: status, received: make(chan struct{}, 64)}
	c.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("collector: read body: %v", err)
			return
		}
		var td tracepb.TracesData
		if err := proto.Unmarshal(body, &td); err != nil {
			t.Errorf("collector: unmarshal TracesData: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}

		c.mu.Lock()
		n := len(c.paths) + 1
		c.paths = append(c.paths, r.URL.Path)
		c.ctypes = append(c.ctypes, r.Header.Get("Content-Type"))
		c.headers = append(c.headers, r.Header.Clone())
		var spans []*tracepb.Span
		for _, rs := range td.ResourceSpans {
			if rs.Resource != nil {
				c.resource = rs.Resource.Attributes
			}
			for _, ss := range rs.ScopeSpans {
				spans = append(spans, ss.Spans...)
			}
		}
		c.batches = append(c.batches, spans)
		code := http.StatusOK
		if c.status != nil {
			code = c.status(n)
		}
		c.mu.Unlock()

		w.WriteHeader(code)
		select {
		case c.received <- struct{}{}:
		default:
		}
	}))
	t.Cleanup(c.Close)
	return c
}

func (c *collector) allSpans() []*tracepb.Span {
	c.mu.Lock()
	defer c.mu.Unlock()
	var out []*tracepb.Span
	for _, b := range c.batches {
		out = append(out, b...)
	}
	return out
}

func (c *collector) requestCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.paths)
}

func attr(spans []*commonpb.KeyValue, key string) *commonpb.AnyValue {
	for _, kv := range spans {
		if kv.Key == key {
			return kv.Value
		}
	}
	return nil
}

func testSpan() *Span {
	s := NewSpan("chat_completion", "agentcc-gateway-stage")
	s.SetAttribute("gen_ai.request.model", "gpt-4o")
	s.SetAttribute("gen_ai.usage.input_tokens", 120)
	s.SetAttribute("gen_ai.cost.total", 0.0042)
	s.SetAttribute("agentcc.is_stream", false)
	s.SetAttribute("metadata", `{"profile_id":"milestone-p1"}`)
	s.SetAttribute("resource.deployment.environment", "stage")
	s.End()
	return s
}

func TestNormalizeID(t *testing.T) {
	tests := []struct {
		name       string
		id         string
		size       int
		wantDecode bool // input is valid hex of the right width and passes through
	}{
		{"hex trace id passes through", "4bf92f3577b34da6a3ce929d0e0e4736", 16, true},
		{"hex span id passes through", "00f067aa0ba902b7", 8, true},
		{"uppercase hex passes through", "4BF92F3577B34DA6A3CE929D0E0E4736", 16, true},
		{"ulid is hashed", "01J8ZQ9K2M3N4P5Q6R7S8T9V0W", 16, false},
		{"non-hex of exact width is hashed", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", 16, false},
		{"short id is hashed", "abc", 16, false},
		{"empty is hashed", "", 8, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := normalizeID(tt.id, tt.size)
			if len(got) != tt.size {
				t.Fatalf("length = %d, want %d", len(got), tt.size)
			}
			if tt.wantDecode {
				want, err := hex.DecodeString(tt.id)
				if err != nil {
					t.Fatalf("test input is not hex: %v", err)
				}
				if hex.EncodeToString(got) != hex.EncodeToString(want) {
					t.Fatalf("got %x, want %x", got, want)
				}
			}
			// Whatever the path, it must be deterministic — otherwise spans of
			// one gateway trace would scatter across OTLP traces.
			if again := normalizeID(tt.id, tt.size); hex.EncodeToString(again) != hex.EncodeToString(got) {
				t.Fatalf("not deterministic: %x then %x", got, again)
			}
		})
	}
}

func TestNormalizeIDDistinguishesInputs(t *testing.T) {
	a := normalizeID("01J8ZQ9K2M3N4P5Q6R7S8T9V0W", 16)
	b := normalizeID("01J8ZQ9K2M3N4P5Q6R7S8T9V0X", 16)
	if hex.EncodeToString(a) == hex.EncodeToString(b) {
		t.Fatal("different trace IDs hashed to the same OTLP trace ID")
	}
}

func TestEncodeSpanAttributes(t *testing.T) {
	s := testSpan()
	s.TraceID = "01J8ZQ9K2M3N4P5Q6R7S8T9V0W" // ULID, as the middleware produces
	pb := encodeSpan(s)

	if len(pb.TraceId) != 16 {
		t.Fatalf("TraceId length = %d, want 16", len(pb.TraceId))
	}
	if len(pb.SpanId) != 8 {
		t.Fatalf("SpanId length = %d, want 8", len(pb.SpanId))
	}
	if pb.Kind != tracepb.Span_SPAN_KIND_CLIENT {
		t.Fatalf("Kind = %v, want CLIENT", pb.Kind)
	}
	if pb.Name != "chat_completion" {
		t.Fatalf("Name = %q", pb.Name)
	}
	if pb.StartTimeUnixNano == 0 || pb.EndTimeUnixNano == 0 {
		t.Fatalf("timestamps not set: start=%d end=%d", pb.StartTimeUnixNano, pb.EndTimeUnixNano)
	}
	if pb.EndTimeUnixNano < pb.StartTimeUnixNano {
		t.Fatal("end before start")
	}
	if pb.Status.Code != tracepb.Status_STATUS_CODE_OK {
		t.Fatalf("Status = %v, want OK", pb.Status.Code)
	}

	if v := attr(pb.Attributes, "gen_ai.request.model"); v.GetStringValue() != "gpt-4o" {
		t.Fatalf("model attribute = %v", v)
	}
	if v := attr(pb.Attributes, "gen_ai.usage.input_tokens"); v.GetIntValue() != 120 {
		t.Fatalf("input_tokens = %v, want int 120", v)
	}
	if v := attr(pb.Attributes, "gen_ai.cost.total"); v.GetDoubleValue() != 0.0042 {
		t.Fatalf("cost = %v, want double 0.0042", v)
	}
	if v := attr(pb.Attributes, "agentcc.is_stream"); v == nil || v.GetBoolValue() {
		t.Fatalf("is_stream = %v, want bool false", v)
	}
	if v := attr(pb.Attributes, "metadata"); v.GetStringValue() != `{"profile_id":"milestone-p1"}` {
		t.Fatalf("caller metadata missing or not a JSON string: %v", v)
	}
	// The gateway trace ID must survive verbatim: it is what response headers
	// and the request-log table carry.
	if v := attr(pb.Attributes, "agentcc.trace_id"); v.GetStringValue() != s.TraceID {
		t.Fatalf("agentcc.trace_id = %v, want %q", v, s.TraceID)
	}
	// resource.* belongs on the Resource, not repeated on every span.
	if v := attr(pb.Attributes, "resource.deployment.environment"); v != nil {
		t.Fatalf("resource-prefixed attribute leaked onto span: %v", v)
	}
}

func TestEncodeSpanError(t *testing.T) {
	s := testSpan()
	s.SetError("upstream 503 from azure")
	pb := encodeSpan(s)

	if pb.Status.Code != tracepb.Status_STATUS_CODE_ERROR {
		t.Fatalf("Status = %v, want ERROR", pb.Status.Code)
	}
	if pb.Status.Message != "upstream 503 from azure" {
		t.Fatalf("Status.Message = %q", pb.Status.Message)
	}
}

func TestEncodeSpanChild(t *testing.T) {
	parent := testSpan()
	child := NewChildSpan("guardrail", parent.TraceID, parent.SpanID)
	child.End()

	pb := encodeSpan(child)
	if len(pb.ParentSpanId) != 8 {
		t.Fatalf("ParentSpanId length = %d, want 8", len(pb.ParentSpanId))
	}
	if hex.EncodeToString(pb.ParentSpanId) != parent.SpanID {
		t.Fatalf("ParentSpanId = %x, want %s", pb.ParentSpanId, parent.SpanID)
	}
	if hex.EncodeToString(pb.TraceId) != parent.TraceID {
		t.Fatal("child span landed on a different trace than its parent")
	}
}

func TestEncodeSpanWithoutTraceIDStaysUnique(t *testing.T) {
	a, b := testSpan(), testSpan()
	a.TraceID, b.TraceID = "", ""
	if hex.EncodeToString(encodeSpan(a).TraceId) == hex.EncodeToString(encodeSpan(b).TraceId) {
		t.Fatal("spans with no trace ID collapsed onto one trace")
	}
}

func TestBuildResource(t *testing.T) {
	r := buildResource("agentcc-gateway-stage", map[string]string{
		"deployment.environment": "stage",
		"service.name":           "should-not-win",
	})
	if v := attr(r.Attributes, "service.name"); v.GetStringValue() != "agentcc-gateway-stage" {
		t.Fatalf("service.name = %v, want the configured service name", v)
	}
	if v := attr(r.Attributes, "deployment.environment"); v.GetStringValue() != "stage" {
		t.Fatalf("deployment.environment = %v", v)
	}
	var count int
	for _, kv := range r.Attributes {
		if kv.Key == "service.name" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("service.name appears %d times, want 1", count)
	}

	if v := attr(buildResource("", nil).Attributes, "service.name"); v.GetStringValue() != "agentcc-gateway" {
		t.Fatalf("empty service name should default, got %v", v)
	}
}

func TestOTLPExporterRoundTrip(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "agentcc-gateway-stage", Resource: map[string]string{"deployment.environment": "stage"}})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	// Below the batch threshold — nothing should have left yet.
	if n := c.requestCount(); n != 0 {
		t.Fatalf("exporter posted %d times before flush; Export must not block on the collector", n)
	}
	if err := e.Shutdown(); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}

	spans := c.allSpans()
	if len(spans) != 1 {
		t.Fatalf("collector got %d spans, want 1", len(spans))
	}
	c.mu.Lock()
	path, ctype, resource := c.paths[0], c.ctypes[0], c.resource
	c.mu.Unlock()

	if path != "/v1/traces" {
		t.Fatalf("path = %q, want /v1/traces", path)
	}
	if ctype != "application/x-protobuf" {
		t.Fatalf("Content-Type = %q", ctype)
	}
	if v := attr(resource, "service.name"); v.GetStringValue() != "agentcc-gateway-stage" {
		t.Fatalf("resource service.name = %v", v)
	}
	if v := attr(resource, "deployment.environment"); v.GetStringValue() != "stage" {
		t.Fatalf("resource deployment.environment = %v", v)
	}
	if v := attr(spans[0].Attributes, "gen_ai.request.model"); v.GetStringValue() != "gpt-4o" {
		t.Fatalf("span lost its model attribute: %v", v)
	}
}

func TestOTLPExporterEndpointPath(t *testing.T) {
	tests := []struct {
		endpoint string
		want     string
	}{
		{"http://collector:4318", "http://collector:4318/v1/traces"},
		{"http://collector:4318/", "http://collector:4318/v1/traces"},
		{"https://otlp.vendor.io/otlp/v1/traces", "https://otlp.vendor.io/otlp/v1/traces"},
	}
	for _, tt := range tests {
		e, err := NewOTLPExporter(OTLPOptions{Endpoint: tt.endpoint, ServiceName: "svc", Resource: nil})
		if err != nil {
			t.Fatalf("NewOTLPExporter(%q): %v", tt.endpoint, err)
		}
		if e.url != tt.want {
			t.Errorf("endpoint %q resolved to %q, want %q", tt.endpoint, e.url, tt.want)
		}
		_ = e.Shutdown()
	}
}

func TestNewOTLPExporterRejectsBadEndpoint(t *testing.T) {
	for _, endpoint := range []string{"", "otel-collector:4318", "://nope"} {
		if _, err := NewOTLPExporter(OTLPOptions{Endpoint: endpoint, ServiceName: "svc", Resource: nil}); err == nil {
			t.Errorf("endpoint %q was accepted, want an error", endpoint)
		}
	}
}

func TestOTLPExporterBatchSizeTriggersFlush(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	defer e.Shutdown()

	batch := make([]*Span, otlpBatchSize)
	for i := range batch {
		batch[i] = testSpan()
	}
	if err := e.Export(batch); err != nil {
		t.Fatalf("Export: %v", err)
	}

	// Reaching the batch size flushes without waiting for the ticker.
	select {
	case <-c.received:
	case <-time.After(5 * time.Second):
		t.Fatal("a full batch did not trigger a flush")
	}
	if n := len(c.allSpans()); n != otlpBatchSize {
		t.Fatalf("collector got %d spans, want %d", n, otlpBatchSize)
	}
}

func TestOTLPExporterDropsWhenQueueFull(t *testing.T) {
	// Point at a URL nothing answers on so the queue actually fills.
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: "http://127.0.0.1:1/v1/traces", ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	// Enqueue directly, bypassing the batch-size flush, to fill the queue.
	e.mu.Lock()
	for i := 0; i < otlpMaxQueue; i++ {
		e.buffer = append(e.buffer, testSpan())
	}
	e.mu.Unlock()

	if err := e.Export([]*Span{testSpan(), testSpan()}); err != nil {
		t.Fatalf("Export must not error on a full queue: %v", err)
	}
	if got := e.Dropped(); got != 2 {
		t.Fatalf("Dropped() = %d, want 2", got)
	}
	e.mu.Lock()
	queued := len(e.buffer)
	e.mu.Unlock()
	if queued != otlpMaxQueue {
		t.Fatalf("queue grew past its ceiling: %d > %d", queued, otlpMaxQueue)
	}
}

func TestOTLPExporterRetriesServerErrors(t *testing.T) {
	// Fail the first attempt with 503, accept the second.
	c := newCollector(t, func(n int) int {
		if n == 1 {
			return http.StatusServiceUnavailable
		}
		return http.StatusOK
	})
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	e.flush() // first attempt: 503, span goes back on the queue
	if e.Dropped() != 0 {
		t.Fatalf("a 5xx dropped spans instead of re-queuing: dropped=%d", e.Dropped())
	}
	e.flush() // second attempt: accepted

	if got := c.requestCount(); got != 2 {
		t.Fatalf("collector saw %d requests, want 2", got)
	}
	if n := len(c.batches[1]); n != 1 {
		t.Fatalf("retry carried %d spans, want the original 1", n)
	}
	_ = e.Shutdown()
}

func TestOTLPExporterGivesUpAfterMaxRetries(t *testing.T) {
	c := newCollector(t, func(int) int { return http.StatusServiceUnavailable })
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	for i := 0; i <= otlpMaxRetries; i++ {
		e.flush()
	}
	if e.Dropped() != 1 {
		t.Fatalf("Dropped() = %d, want 1 after %d consecutive failures", e.Dropped(), otlpMaxRetries+1)
	}
	e.mu.Lock()
	queued := len(e.buffer)
	e.mu.Unlock()
	if queued != 0 {
		t.Fatalf("%d spans still queued after giving up", queued)
	}
	_ = e.Shutdown()
}

func TestOTLPExporterDropsClientErrorsImmediately(t *testing.T) {
	c := newCollector(t, func(int) int { return http.StatusBadRequest })
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	e.flush()

	if e.Dropped() != 1 {
		t.Fatalf("Dropped() = %d, want 1 — a 4xx cannot be fixed by resending", e.Dropped())
	}
	e.mu.Lock()
	queued := len(e.buffer)
	e.mu.Unlock()
	if queued != 0 {
		t.Fatalf("%d spans re-queued after a 4xx", queued)
	}
	if got := c.requestCount(); got != 1 {
		t.Fatalf("collector saw %d requests, want 1", got)
	}
	_ = e.Shutdown()
}

func TestOTLPExporterRetriesRateLimits(t *testing.T) {
	// A collector under load answers 429; the batch is still perfectly good.
	c := newCollector(t, func(n int) int {
		if n == 1 {
			return http.StatusTooManyRequests
		}
		return http.StatusOK
	})
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	e.flush() // 429 — re-queued, not dropped with the rest of the 4xx family
	if e.Dropped() != 0 {
		t.Fatalf("a 429 dropped spans instead of re-queuing: dropped=%d", e.Dropped())
	}
	e.flush()

	if got := c.requestCount(); got != 2 {
		t.Fatalf("collector saw %d requests, want 2", got)
	}
	if n := len(c.batches[1]); n != 1 {
		t.Fatalf("retry carried %d spans, want the original 1", n)
	}
	_ = e.Shutdown()
}

// An unencodable span takes its whole batch with it — proto.Marshal fails the
// message, not the field. Nothing can be sent, but the loss has to show up in
// Dropped(), which exists precisely so a silent hole is visible.
func TestOTLPExporterCountsABatchLostToEncoding(t *testing.T) {
	c := newCollector(t, func(int) int { return http.StatusOK })
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	bad := testSpan()
	bad.Attributes["broken"] = "\xff\xfe" // not valid UTF-8; proto strings must be
	if err := e.Export([]*Span{testSpan(), bad}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	e.flush()

	if got := c.requestCount(); got != 0 {
		t.Fatalf("collector saw %d requests, want 0 — the batch cannot encode", got)
	}
	if e.Dropped() != 2 {
		t.Fatalf("Dropped() = %d, want 2 — the whole batch was lost", e.Dropped())
	}
	_ = e.Shutdown()
}

// A batch coming back from a failed send is the one path into the queue that
// does not go through Export, so the byte ceiling has to be enforced here too
// or a retry can push the queue past the bound that caps its memory.
func TestOTLPExporterRetryHoldsTheByteCeiling(t *testing.T) {
	c := newCollector(t, func(int) int { return http.StatusOK })
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	defer func() { _ = e.Shutdown() }()

	big := func() *Span {
		s := NewSpan("chat_completion", "svc")
		s.SetAttribute("input.value", strings.Repeat("x", 4096))
		s.End()
		return s
	}

	// Park the queue just under the ceiling, with room for one of these spans.
	e.mu.Lock()
	e.bufferBytes = otlpMaxQueueBytes - spanSize(big())
	e.mu.Unlock()

	e.retry([]*Span{big(), big(), big()}, "test", slog.String("k", "v"))

	e.mu.Lock()
	queued, bytes := len(e.buffer), e.bufferBytes
	e.mu.Unlock()

	if bytes > otlpMaxQueueBytes {
		t.Errorf("bufferBytes = %d, over the %d ceiling", bytes, otlpMaxQueueBytes)
	}
	if queued != 1 {
		t.Errorf("re-enqueued %d spans, want the 1 that fits", queued)
	}
	if e.Dropped() != 2 {
		t.Errorf("Dropped() = %d, want the 2 that did not fit", e.Dropped())
	}
}

func TestOTLPExporterShutdownIsIdempotent(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	if err := e.Shutdown(); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}
	if err := e.Shutdown(); err != nil {
		t.Fatalf("second Shutdown: %v", err)
	}
	if n := len(c.allSpans()); n != 1 {
		t.Fatalf("collector got %d spans, want 1", n)
	}
}

func TestOTLPExporterConcurrentExport(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Resource: nil})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	// IsPostParallel is true, so Export is called from parallel post-plugins.
	const writers, each = 8, 50
	var wg sync.WaitGroup
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < each; j++ {
				_ = e.Export([]*Span{testSpan()})
			}
		}()
	}
	wg.Wait()
	if err := e.Shutdown(); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}

	if got, want := len(c.allSpans())+int(e.Dropped()), writers*each; got != want {
		t.Fatalf("accounted for %d spans, want %d", got, want)
	}
}

// Hosted collectors — including FutureAGI's own /v1/traces — authenticate by
// header. Without these the exporter 401s and silently discards every span.
func TestOTLPExporterSendsConfiguredHeaders(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{
		Endpoint:    c.URL,
		ServiceName: "svc",
		Headers: map[string]string{
			"X-Api-Key":    "api-key-value",
			"X-Secret-Key": "secret-key-value",
		},
	})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	if err := e.Shutdown(); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}

	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.headers) != 1 {
		t.Fatalf("collector saw %d requests, want 1", len(c.headers))
	}
	if got := c.headers[0].Get("X-Api-Key"); got != "api-key-value" {
		t.Errorf("X-Api-Key = %q", got)
	}
	if got := c.headers[0].Get("X-Secret-Key"); got != "secret-key-value" {
		t.Errorf("X-Secret-Key = %q", got)
	}
	// Configured headers must not be able to break the payload contract.
	if got := c.headers[0].Get("Content-Type"); got != otlpContentType {
		t.Errorf("Content-Type = %q, want %q", got, otlpContentType)
	}
}

// A caller mutating the map it passed in must not change what the exporter sends.
func TestOTLPExporterCopiesHeaders(t *testing.T) {
	c := newCollector(t, nil)
	headers := map[string]string{"X-Api-Key": "original"}
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc", Headers: headers})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	headers["X-Api-Key"] = "mutated"

	if err := e.Export([]*Span{testSpan()}); err != nil {
		t.Fatalf("Export: %v", err)
	}
	_ = e.Shutdown()

	c.mu.Lock()
	defer c.mu.Unlock()
	if got := c.headers[0].Get("X-Api-Key"); got != "original" {
		t.Errorf("X-Api-Key = %q, want the value captured at construction", got)
	}
}

// Credentials must never reach a log line; only header names may.
func TestHeaderNamesExcludesValues(t *testing.T) {
	names := headerNames(map[string]string{"X-Secret-Key": "s3cret", "X-Api-Key": "k3y"})
	if len(names) != 2 || names[0] != "X-Api-Key" || names[1] != "X-Secret-Key" {
		t.Fatalf("headerNames = %v, want sorted names", names)
	}
	for _, n := range names {
		if n == "s3cret" || n == "k3y" {
			t.Fatal("headerNames leaked a credential value")
		}
	}
}

func spanWithBody(bytes int) *Span {
	s := testSpan()
	s.SetAttribute("input.value", strings.Repeat("x", bytes))
	return s
}

// Spans carrying prompts are orders of magnitude larger than ordinary ones, so
// a count-only ceiling lets the queue grow to hundreds of megabytes while a
// collector is unreachable.
func TestOTLPExporterQueueBoundedByBytes(t *testing.T) {
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: "http://127.0.0.1:1/v1/traces", ServiceName: "svc"})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	defer e.Shutdown()

	// Well under the span-count ceiling, well over the byte ceiling.
	const each = 1 << 20
	spans := make([]*Span, 0, 128)
	for i := 0; i < 128; i++ {
		spans = append(spans, spanWithBody(each))
	}
	if err := e.Export(spans); err != nil {
		t.Fatalf("Export: %v", err)
	}

	e.mu.Lock()
	queued, queuedBytes := len(e.buffer), e.bufferBytes
	e.mu.Unlock()

	if queued == len(spans) {
		t.Error("byte ceiling never applied — the whole batch was queued")
	}
	if queuedBytes > otlpMaxQueueBytes {
		t.Errorf("queued %d bytes, over the %d ceiling", queuedBytes, otlpMaxQueueBytes)
	}
	if e.Dropped() == 0 {
		t.Error("dropped spans not counted")
	}
}

// An oversized batch is split locally rather than posted and rejected: a 413
// costs a round trip to learn something the exporter can measure itself.
func TestOTLPExporterSplitsOversizedPayload(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc"})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}

	// Four spans of 3 MiB: one batch would exceed the 8 MiB payload limit.
	spans := []*Span{spanWithBody(3 << 20), spanWithBody(3 << 20), spanWithBody(3 << 20), spanWithBody(3 << 20)}
	e.mu.Lock()
	e.buffer = append(e.buffer, spans...)
	e.mu.Unlock()
	e.flush()

	if got := c.requestCount(); got < 2 {
		t.Fatalf("collector saw %d requests, want the batch split across at least 2", got)
	}
	if got := len(c.allSpans()); got != len(spans) {
		t.Errorf("collector received %d spans, want all %d", got, len(spans))
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	for i, h := range c.headers {
		if h.Get("Content-Type") != otlpContentType {
			t.Errorf("split request %d lost its content type", i)
		}
	}
	if e.Dropped() != 0 {
		t.Errorf("splitting dropped %d spans", e.Dropped())
	}
	_ = e.Shutdown()
}

// A body large enough to make one span unsendable is dropped alone, not
// retried forever and not taking a batch with it.
func TestOTLPExporterDropsSingleOversizedSpan(t *testing.T) {
	c := newCollector(t, nil)
	e, err := NewOTLPExporter(OTLPOptions{Endpoint: c.URL, ServiceName: "svc"})
	if err != nil {
		t.Fatalf("NewOTLPExporter: %v", err)
	}
	e.mu.Lock()
	e.buffer = append(e.buffer, spanWithBody(otlpMaxBodyBytes+(1<<20)))
	e.mu.Unlock()
	e.flush()

	if got := c.requestCount(); got != 0 {
		t.Errorf("posted %d oversized requests, want 0", got)
	}
	if e.Dropped() != 1 {
		t.Errorf("Dropped() = %d, want 1", e.Dropped())
	}
	_ = e.Shutdown()
}
