package otel

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// captureExporter captures exported spans for assertions.
type captureExporter struct {
	mu    sync.Mutex
	spans []*otelpkg.Span
}

func (c *captureExporter) Export(spans []*otelpkg.Span) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.spans = append(c.spans, spans...)
	return nil
}

func (c *captureExporter) Shutdown() error { return nil }

func (c *captureExporter) Spans() []*otelpkg.Span {
	c.mu.Lock()
	defer c.mu.Unlock()
	cp := make([]*otelpkg.Span, len(c.spans))
	copy(cp, c.spans)
	return cp
}

func newTestRC() *models.RequestContext {
	maxTokens := 1000
	return &models.RequestContext{
		RequestID: "req-123",
		TraceID:   "trace-abc",
		StartTime: time.Now(),
		Model:     "gpt-4",
		Provider:  "openai",
		IsStream:  false,
		UserID:    "user-1",
		SessionID: "session-1",
		Metadata:  map[string]string{},
		Timings:   map[string]time.Duration{},
		Errors:    []error{},
		Request: &models.ChatCompletionRequest{
			Model:     "gpt-4",
			MaxTokens: &maxTokens,
		},
		Response: &models.ChatCompletionResponse{
			Usage: &models.Usage{
				PromptTokens:     150,
				CompletionTokens: 50,
			},
		},
	}
}

func TestPlugin_Disabled(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, false)

	rc := newTestRC()
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("disabled plugin should continue")
	}

	result = p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatal("disabled plugin should continue")
	}

	if len(exp.Spans()) != 0 {
		t.Fatal("disabled plugin should not export spans")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	if p.Name() != "otel" {
		t.Fatalf("Name() = %q, want otel", p.Name())
	}
	if p.Priority() != 999 {
		t.Fatalf("Priority() = %d, want 999", p.Priority())
	}
}

func TestPlugin_FullSpanLifecycle(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	rc := newTestRC()
	rc.ResolvedModel = "gpt-4-0613"
	rc.Metadata["cost"] = "0.005000"
	rc.Metadata["cache_status"] = "miss"
	rc.Timings["ttft"] = 120 * time.Millisecond
	rc.Flags.GuardrailTriggered = true
	rc.Metadata["budget_remaining"] = "95.50"

	// Pre-request.
	result := p.ProcessRequest(ctx, rc)
	if result.Action != pipeline.Continue {
		t.Fatal("ProcessRequest should continue")
	}

	// Post-response.
	result = p.ProcessResponse(ctx, rc)
	if result.Action != pipeline.Continue {
		t.Fatal("ProcessResponse should continue")
	}

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}

	s := spans[0]
	if s.TraceID != "trace-abc" {
		t.Fatalf("TraceID = %q, want trace-abc", s.TraceID)
	}
	if s.Name != "chat_completion" {
		t.Fatalf("Name = %q, want chat_completion", s.Name)
	}
	if s.Status != "OK" {
		t.Fatalf("Status = %q, want OK", s.Status)
	}

	// Check attributes.
	assertAttr(t, s, "gen_ai.span.kind", "llm")
	assertAttr(t, s, "gen_ai.operation.name", "chat")
	assertAttr(t, s, "gen_ai.system", "openai")
	assertAttr(t, s, "gen_ai.provider.name", "openai")
	assertAttr(t, s, "gen_ai.request.model", "gpt-4")
	assertAttr(t, s, "gen_ai.response.model", "gpt-4-0613")
	assertAttr(t, s, "gen_ai.usage.input_tokens", 150)
	assertAttr(t, s, "gen_ai.usage.output_tokens", 50)
	assertAttr(t, s, "gen_ai.usage.total_tokens", 200)
	assertAttr(t, s, "gen_ai.request.max_tokens", 1000)
	assertAttr(t, s, "gen_ai.cost.total", 0.005)
	assertAttr(t, s, "gen_ai.server.time_to_first_token", 0.12)
	assertAttr(t, s, "session.id", "session-1")
	assertAttr(t, s, "user.id", "user-1")
	assertAttr(t, s, "agentcc.request_id", "req-123")
	assertAttr(t, s, "agentcc.is_stream", false)
	assertAttr(t, s, "agentcc.cache_status", "miss")
	assertAttr(t, s, "agentcc.guardrail_triggered", true)
	assertAttr(t, s, "agentcc.budget_remaining", 95.50)

	if s.EndTime.IsZero() {
		t.Fatal("span should be ended")
	}
}

func TestPlugin_ErrorSpan(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	rc := newTestRC()
	rc.Errors = append(rc.Errors, errors.New("provider timeout"))

	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	if spans[0].Status != "ERROR" {
		t.Fatalf("Status = %q, want ERROR", spans[0].Status)
	}
	assertAttr(t, spans[0], "error.message", "provider timeout")

	if p.Metrics().ErrorCount.Load() != 1 {
		t.Fatalf("ErrorCount = %d, want 1", p.Metrics().ErrorCount.Load())
	}
}

func TestPlugin_NilResponse(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	rc := newTestRC()
	rc.Response = nil

	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}

	// Should not have token attributes.
	if _, ok := spans[0].Attributes["gen_ai.usage.input_tokens"]; ok {
		t.Fatal("should not set input_tokens when response is nil")
	}
}

func TestPlugin_NilUsage(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	rc := newTestRC()
	rc.Response = &models.ChatCompletionResponse{Usage: nil}

	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	if _, ok := spans[0].Attributes["gen_ai.usage.input_tokens"]; ok {
		t.Fatal("should not set input_tokens when usage is nil")
	}
}

func TestPlugin_CacheHitMetrics(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	// Cache hit.
	rc1 := newTestRC()
	rc1.RequestID = "req-hit"
	rc1.Metadata["cache_status"] = "hit_exact"
	p.ProcessRequest(ctx, rc1)
	p.ProcessResponse(ctx, rc1)

	// Cache miss.
	rc2 := newTestRC()
	rc2.RequestID = "req-miss"
	rc2.Metadata["cache_status"] = "miss"
	p.ProcessRequest(ctx, rc2)
	p.ProcessResponse(ctx, rc2)

	// Semantic hit.
	rc3 := newTestRC()
	rc3.RequestID = "req-sem"
	rc3.Metadata["cache_status"] = "hit_semantic"
	p.ProcessRequest(ctx, rc3)
	p.ProcessResponse(ctx, rc3)

	m := p.Metrics().Snapshot()
	if m.CacheHits != 2 {
		t.Fatalf("CacheHits = %d, want 2", m.CacheHits)
	}
	if m.CacheMisses != 1 {
		t.Fatalf("CacheMisses = %d, want 1", m.CacheMisses)
	}
}

func TestPlugin_TokenMetrics(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	for i := 0; i < 3; i++ {
		rc := newTestRC()
		rc.RequestID = fmt.Sprintf("req-%d", i)
		p.ProcessRequest(ctx, rc)
		p.ProcessResponse(ctx, rc)
	}

	m := p.Metrics().Snapshot()
	if m.RequestCount != 3 {
		t.Fatalf("RequestCount = %d, want 3", m.RequestCount)
	}
	if m.InputTokens != 450 { // 150 * 3
		t.Fatalf("InputTokens = %d, want 450", m.InputTokens)
	}
	if m.OutputTokens != 150 { // 50 * 3
		t.Fatalf("OutputTokens = %d, want 150", m.OutputTokens)
	}
}

func TestPlugin_SampleRate_Zero(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 0.0, true)
	ctx := context.Background()

	rc := newTestRC()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	if len(exp.Spans()) != 0 {
		t.Fatal("sample_rate 0 should not create spans")
	}

	// Metrics should still be recorded.
	if p.Metrics().RequestCount.Load() != 1 {
		t.Fatalf("RequestCount = %d, want 1 even when not sampled", p.Metrics().RequestCount.Load())
	}
}

func TestPlugin_SampleRate_Full(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	for i := 0; i < 10; i++ {
		rc := newTestRC()
		rc.RequestID = fmt.Sprintf("req-%d", i)
		p.ProcessRequest(ctx, rc)
		p.ProcessResponse(ctx, rc)
	}

	if len(exp.Spans()) != 10 {
		t.Fatalf("sample_rate 1.0 should create all spans, got %d", len(exp.Spans()))
	}
}

func TestPlugin_SampleRate_Partial(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 0.5, true)
	ctx := context.Background()

	for i := 0; i < 100; i++ {
		rc := newTestRC()
		rc.RequestID = fmt.Sprintf("req-%06d", i)
		p.ProcessRequest(ctx, rc)
		p.ProcessResponse(ctx, rc)
	}

	sampled := len(exp.Spans())
	// With 50% sample rate over 100 requests, expect roughly 30-70 sampled.
	if sampled < 20 || sampled > 80 {
		t.Fatalf("expected ~50 sampled spans, got %d", sampled)
	}
}

func TestPlugin_SampleRate_Deterministic(t *testing.T) {
	exp1 := &captureExporter{}
	p1 := NewWithExporter(exp1, 0.5, true)

	exp2 := &captureExporter{}
	p2 := NewWithExporter(exp2, 0.5, true)

	ctx := context.Background()

	// Same request IDs should produce same sampling decisions.
	for i := 0; i < 50; i++ {
		rc1 := newTestRC()
		rc1.RequestID = fmt.Sprintf("req-%d", i)
		p1.ProcessRequest(ctx, rc1)
		p1.ProcessResponse(ctx, rc1)

		rc2 := newTestRC()
		rc2.RequestID = fmt.Sprintf("req-%d", i)
		p2.ProcessRequest(ctx, rc2)
		p2.ProcessResponse(ctx, rc2)
	}

	if len(exp1.Spans()) != len(exp2.Spans()) {
		t.Fatalf("deterministic sampling: %d vs %d spans", len(exp1.Spans()), len(exp2.Spans()))
	}
}

func TestPlugin_NoTraceID_GeneratesOne(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	rc := newTestRC()
	rc.TraceID = "" // No trace ID provided.

	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	if spans[0].TraceID == "" {
		t.Fatal("span should have a generated TraceID")
	}
	if len(spans[0].TraceID) != 32 {
		t.Fatalf("TraceID should be 32 hex chars, got %d", len(spans[0].TraceID))
	}
}

func TestPlugin_Concurrent(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	ctx := context.Background()

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			rc := newTestRC()
			rc.RequestID = fmt.Sprintf("concurrent-%d", n)
			p.ProcessRequest(ctx, rc)
			p.ProcessResponse(ctx, rc)
		}(i)
	}
	wg.Wait()

	if len(exp.Spans()) != 50 {
		t.Fatalf("expected 50 spans, got %d", len(exp.Spans()))
	}
	if p.Metrics().RequestCount.Load() != 50 {
		t.Fatalf("RequestCount = %d, want 50", p.Metrics().RequestCount.Load())
	}
}

func TestPlugin_StdoutExporterJSON(t *testing.T) {
	var buf bytes.Buffer
	exp := otelpkg.NewStdoutExporter(&buf)
	p := &Plugin{
		exporter:    exp,
		metrics:     &otelpkg.Metrics{},
		sampleRate:  1.0,
		enabled:     true,
		serviceName: "agentcc-gateway",
	}

	rc := newTestRC()
	rc.Metadata["cost"] = "0.001234"
	p.ProcessRequest(context.Background(), rc)
	p.ProcessResponse(context.Background(), rc)

	output := buf.String()
	if output == "" {
		t.Fatal("stdout exporter should produce output")
	}

	var decoded map[string]interface{}
	if err := json.Unmarshal([]byte(strings.TrimSpace(output)), &decoded); err != nil {
		t.Fatalf("output is not valid JSON: %v\n%s", err, output)
	}

	if decoded["name"] != "chat_completion" {
		t.Fatalf("name = %v, want chat_completion", decoded["name"])
	}
}

func TestPlugin_Close(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	p.Close() // Should not panic.
}

func assertAttr(t *testing.T, s *otelpkg.Span, key string, want interface{}) {
	t.Helper()
	got, ok := s.Attributes[key]
	if !ok {
		t.Fatalf("attribute %q not set", key)
	}
	if fmt.Sprintf("%v", got) != fmt.Sprintf("%v", want) {
		t.Fatalf("attribute %q = %v (%T), want %v (%T)", key, got, got, want, want)
	}
}

func TestPlugin_CustomMetadataOnSpan(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)

	rc := newTestRC()
	// Caller-supplied dimensions, as parsed from x-agentcc-metadata.
	rc.Metadata["profile_id"] = "milestone-p1"
	rc.Metadata["business_id"] = "biz-42"
	rc.CustomMetadataKeys = []string{"profile_id", "business_id"}
	// Written by the cost plugin, not by the caller — must stay unprefixed.
	rc.Metadata["cost"] = "0.0042"

	ctx := context.Background()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("got %d spans, want 1", len(spans))
	}
	attrs := spans[0].Attributes

	// One JSON object under the conventional `metadata` key — a Go map would
	// reach the consumer as a Go repr, not JSON.
	raw, ok := attrs["metadata"].(string)
	if !ok {
		t.Fatalf("metadata = %#v, want a JSON string", attrs["metadata"])
	}
	var got map[string]string
	if err := json.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("metadata is not valid JSON (%q): %v", raw, err)
	}
	if got["profile_id"] != "milestone-p1" || got["business_id"] != "biz-42" {
		t.Errorf("metadata = %v", got)
	}
	// Only caller keys — plugin-written metadata must not appear as caller input.
	if _, leaked := got["cost"]; leaked {
		t.Error("plugin-written metadata was exported as caller metadata")
	}
	if _, leaked := attrs["profile_id"]; leaked {
		t.Error("caller metadata leaked into the span's top-level namespace")
	}
	if attrs["gen_ai.cost.total"] != 0.0042 {
		t.Errorf("gen_ai.cost.total = %v, want 0.0042", attrs["gen_ai.cost.total"])
	}
}

func TestPlugin_NoCustomMetadata(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)

	rc := newTestRC()
	rc.Metadata["cost"] = "0.01" // internal only, no CustomMetadataKeys

	ctx := context.Background()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	spans := exp.Spans()
	if len(spans) != 1 {
		t.Fatalf("got %d spans, want 1", len(spans))
	}
	if v, ok := spans[0].Attributes["metadata"]; ok {
		t.Errorf("metadata attribute set with no caller input: %v", v)
	}
}

// The platform files a span by gen_ai.span.kind and falls back to UNKNOWN, so
// every endpoint the gateway serves must produce a kind it recognises.
func TestPlugin_SpanKindPerEndpoint(t *testing.T) {
	known := map[string]bool{
		"llm": true, "chain": true, "tool": true, "retriever": true,
		"embedding": true, "agent": true, "reranker": true, "guardrail": true,
		"evaluator": true, "conversation": true,
	}
	endpoints := []struct {
		endpointType string
		wantKind     string
	}{
		{"", "llm"},
		{"chat", "llm"},
		{"completion", "llm"},
		{"responses", "llm"},
		{"assistants", "llm"},
		{"anthropic_messages", "llm"},
		{"genai_generate", "llm"},
		{"genai_stream", "llm"},
		{"embedding", "embedding"},
		{"genai_embed", "embedding"},
		{"rerank", "reranker"},
		{"search", "retriever"},
		{"vector_stores", "retriever"},
		{"image", "llm"},
		{"speech", "llm"},
		{"transcription", "llm"},
		{"translation", "llm"},
		{"ocr", "llm"},
		{"video", "llm"},
	}

	for _, e := range endpoints {
		t.Run(e.endpointType, func(t *testing.T) {
			exp := &captureExporter{}
			p := NewWithExporter(exp, 1.0, true)
			rc := newTestRC()
			rc.EndpointType = e.endpointType

			ctx := context.Background()
			p.ProcessRequest(ctx, rc)
			p.ProcessResponse(ctx, rc)

			attrs := exp.Spans()[0].Attributes
			kind, _ := attrs["gen_ai.span.kind"].(string)
			if kind != e.wantKind {
				t.Errorf("gen_ai.span.kind = %q, want %q", kind, e.wantKind)
			}
			if !known[kind] {
				t.Errorf("gen_ai.span.kind %q is not one the platform recognises — the span files as UNKNOWN", kind)
			}
			if op, _ := attrs["gen_ai.operation.name"].(string); op == "" {
				t.Error("gen_ai.operation.name is empty")
			}
		})
	}
}

// gen_ai.system is an accepted alias for the provider, so naming the gateway
// there would record "agentcc-gateway" as the provider of every call.
func TestPlugin_ProviderAttributes(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	rc := newTestRC()
	rc.Provider = "azure"

	ctx := context.Background()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	attrs := exp.Spans()[0].Attributes
	if attrs["gen_ai.provider.name"] != "azure" {
		t.Errorf("gen_ai.provider.name = %v, want azure", attrs["gen_ai.provider.name"])
	}
	if attrs["gen_ai.system"] != "azure" {
		t.Errorf("gen_ai.system = %v, want the upstream provider", attrs["gen_ai.system"])
	}
}

// Session and user are read from these literal keys, not through the alias
// registry, so gen_ai.conversation.id / enduser.id would silently not land.
func TestPlugin_ConventionalAttributeNames(t *testing.T) {
	exp := &captureExporter{}
	p := NewWithExporter(exp, 1.0, true)
	rc := newTestRC()
	rc.ResolvedModel = "gpt-4-0613"

	ctx := context.Background()
	p.ProcessRequest(ctx, rc)
	p.ProcessResponse(ctx, rc)

	attrs := exp.Spans()[0].Attributes
	for _, want := range []string{
		"gen_ai.span.kind", "gen_ai.operation.name", "gen_ai.request.model",
		"gen_ai.response.model", "gen_ai.provider.name", "gen_ai.system",
		"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
		"gen_ai.usage.total_tokens", "gen_ai.client.operation.duration",
		"session.id", "user.id",
	} {
		if _, ok := attrs[want]; !ok {
			t.Errorf("missing conventional attribute %q", want)
		}
	}
	// Superseded names must be gone, not emitted alongside.
	for _, gone := range []string{
		"gen_ai.provider", "agentcc.cost", "agentcc.session_id", "agentcc.user_id",
		"agentcc.duration_ms", "agentcc.ttft_ms",
	} {
		if v, ok := attrs[gone]; ok {
			t.Errorf("superseded attribute %q still emitted: %v", gone, v)
		}
	}
	// Gateway-only facts keep the agentcc namespace — no platform equivalent.
	for _, want := range []string{"agentcc.request_id", "agentcc.is_stream"} {
		if _, ok := attrs[want]; !ok {
			t.Errorf("missing gateway attribute %q", want)
		}
	}
}

func TestNew_OTLPExporterSelected(t *testing.T) {
	p := New(config.OTelConfig{
		Enabled:     true,
		Exporter:    "otlp",
		Endpoint:    "http://otel-collector:4318",
		ServiceName: "agentcc-gateway-stage",
	})
	defer p.Close()

	if _, ok := p.exporter.(*otelpkg.OTLPExporter); !ok {
		t.Fatalf("exporter = %T, want *otel.OTLPExporter", p.exporter)
	}
}

func TestNew_OTLPBadEndpointFallsBackToStdout(t *testing.T) {
	// Config.Validate normally catches this; the plugin must still degrade to
	// stdout rather than nil-panic on the first request.
	p := New(config.OTelConfig{Enabled: true, Exporter: "otlp", Endpoint: "not-a-url"})
	defer p.Close()

	if _, ok := p.exporter.(*otelpkg.StdoutExporter); !ok {
		t.Fatalf("exporter = %T, want *otel.StdoutExporter", p.exporter)
	}
}

func TestNew_UnknownExporterFallsBackToStdout(t *testing.T) {
	p := New(config.OTelConfig{Enabled: true, Exporter: "jaeger"})
	defer p.Close()

	if _, ok := p.exporter.(*otelpkg.StdoutExporter); !ok {
		t.Fatalf("exporter = %T, want *otel.StdoutExporter", p.exporter)
	}
}
