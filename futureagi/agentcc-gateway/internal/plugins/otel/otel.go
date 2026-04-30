package otel

import (
	"context"
	"hash/fnv"
	"log/slog"
	"os"
	"strconv"
	"strings"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin is a pipeline plugin that creates OTel-compatible trace spans and records metrics.
type Plugin struct {
	exporter    otelpkg.SpanExporter
	metrics     *otelpkg.Metrics
	sampleRate  float64
	enabled     bool
	serviceName string
	attributes  map[string]string

	// spans stores in-flight spans keyed by request ID.
	spans sync.Map
}

// New creates an OTel plugin from config.
func New(cfg config.OTelConfig) *Plugin {
	var exp otelpkg.SpanExporter
	switch strings.ToLower(cfg.Exporter) {
	case "stdout", "":
		exp = otelpkg.NewStdoutExporter(os.Stdout)
	default:
		slog.Warn("unknown otel exporter, falling back to stdout", "exporter", cfg.Exporter)
		exp = otelpkg.NewStdoutExporter(os.Stdout)
	}

	sampleRate := cfg.SampleRate
	if sampleRate <= 0 {
		sampleRate = 1.0
	}

	serviceName := cfg.ServiceName
	if serviceName == "" {
		serviceName = "agentcc-gateway"
	}

	return &Plugin{
		exporter:    exp,
		metrics:     &otelpkg.Metrics{},
		sampleRate:  sampleRate,
		enabled:     cfg.Enabled,
		serviceName: serviceName,
		attributes:  cfg.Attributes,
	}
}

// NewWithExporter creates a plugin with a specific exporter (for testing).
func NewWithExporter(exp otelpkg.SpanExporter, sampleRate float64, enabled bool) *Plugin {
	return &Plugin{
		exporter:    exp,
		metrics:     &otelpkg.Metrics{},
		sampleRate:  sampleRate,
		enabled:     enabled,
		serviceName: "agentcc-gateway",
	}
}

func (p *Plugin) Name() string         { return "otel" }
func (p *Plugin) Priority() int        { return 999 }
func (p *Plugin) IsPostParallel() bool { return true } // Span export, safe to parallelize.

// Metrics returns the plugin's metrics counters.
func (p *Plugin) Metrics() *otelpkg.Metrics {
	return p.metrics
}

// ProcessRequest creates a trace span and stores it for the request.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}

	if !p.shouldSample(rc.RequestID) {
		return pipeline.ResultContinue()
	}

	span := otelpkg.NewSpan("chat_completion", p.serviceName)

	// Use request's TraceID if set, otherwise keep the generated one.
	if rc.TraceID != "" {
		span.TraceID = rc.TraceID
	}

	// Set initial attributes.
	span.SetAttribute("gen_ai.system", "agentcc-gateway")
	span.SetAttribute("gen_ai.request.model", rc.Model)
	span.SetAttribute("agentcc.request_id", rc.RequestID)
	span.SetAttribute("agentcc.is_stream", rc.IsStream)

	if rc.UserID != "" {
		span.SetAttribute("agentcc.user_id", rc.UserID)
	}
	if rc.SessionID != "" {
		span.SetAttribute("agentcc.session_id", rc.SessionID)
	}

	if rc.Request != nil && rc.Request.MaxTokens != nil {
		span.SetAttribute("gen_ai.request.max_tokens", *rc.Request.MaxTokens)
	}

	// Add resource attributes from config.
	for k, v := range p.attributes {
		span.SetAttribute("resource."+k, v)
	}

	p.spans.Store(rc.RequestID, span)
	return pipeline.ResultContinue()
}

// ProcessResponse populates the span with response data, exports it, and records metrics.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}

	// Always count requests in metrics.
	p.metrics.RequestCount.Add(1)

	// Load span (may not exist if not sampled).
	raw, ok := p.spans.LoadAndDelete(rc.RequestID)
	if !ok {
		// Not sampled — still record metrics.
		p.recordMetrics(rc)
		return pipeline.ResultContinue()
	}
	span := raw.(*otelpkg.Span)

	// Provider / resolved model.
	if rc.Provider != "" {
		span.SetAttribute("gen_ai.provider", rc.Provider)
	}
	if rc.ResolvedModel != "" {
		span.SetAttribute("gen_ai.response.model", rc.ResolvedModel)
	}

	// Token usage.
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := rc.Response.Usage
		span.SetAttribute("gen_ai.usage.input_tokens", usage.PromptTokens)
		span.SetAttribute("gen_ai.usage.output_tokens", usage.CompletionTokens)
		p.metrics.InputTokens.Add(int64(usage.PromptTokens))
		p.metrics.OutputTokens.Add(int64(usage.CompletionTokens))
	}

	// Cost from metadata (set by cost plugin).
	if costStr, ok := rc.Metadata["cost"]; ok {
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil {
			span.SetAttribute("agentcc.cost", cost)
		}
	}

	// Cache status from metadata.
	if cacheStatus, ok := rc.Metadata["cache_status"]; ok {
		span.SetAttribute("agentcc.cache_status", cacheStatus)
		if strings.HasPrefix(cacheStatus, "hit") {
			p.metrics.CacheHits.Add(1)
		} else if cacheStatus == "miss" {
			p.metrics.CacheMisses.Add(1)
		}
	}

	// TTFT from timings.
	if ttft, ok := rc.Timings["ttft"]; ok {
		span.SetAttribute("agentcc.ttft_ms", float64(ttft.Milliseconds()))
	}

	// Total duration.
	duration := rc.Elapsed()
	span.SetAttribute("agentcc.duration_ms", float64(duration.Milliseconds()))

	// Guardrail status.
	if rc.Flags.GuardrailTriggered {
		span.SetAttribute("agentcc.guardrail_triggered", true)
	}

	// Budget remaining from metadata.
	if br, ok := rc.Metadata["budget_remaining"]; ok {
		if remaining, err := strconv.ParseFloat(br, 64); err == nil {
			span.SetAttribute("agentcc.budget_remaining", remaining)
		}
	}

	// Errors.
	if len(rc.Errors) > 0 {
		span.SetError(rc.Errors[0].Error())
		p.metrics.ErrorCount.Add(1)
	}

	span.End()

	// Export span.
	if err := p.exporter.Export([]*otelpkg.Span{span}); err != nil {
		slog.Warn("otel export error", "error", err, "request_id", rc.RequestID)
	}

	return pipeline.ResultContinue()
}

// recordMetrics records metrics for non-sampled requests.
func (p *Plugin) recordMetrics(rc *models.RequestContext) {
	if rc.Response != nil && rc.Response.Usage != nil {
		p.metrics.InputTokens.Add(int64(rc.Response.Usage.PromptTokens))
		p.metrics.OutputTokens.Add(int64(rc.Response.Usage.CompletionTokens))
	}
	if cacheStatus, ok := rc.Metadata["cache_status"]; ok {
		if strings.HasPrefix(cacheStatus, "hit") {
			p.metrics.CacheHits.Add(1)
		} else if cacheStatus == "miss" {
			p.metrics.CacheMisses.Add(1)
		}
	}
	if len(rc.Errors) > 0 {
		p.metrics.ErrorCount.Add(1)
	}
}

// Close shuts down the exporter.
func (p *Plugin) Close() {
	if p.exporter != nil {
		_ = p.exporter.Shutdown()
	}
}

// shouldSample uses a deterministic hash of request ID for sampling.
func (p *Plugin) shouldSample(requestID string) bool {
	if p.sampleRate >= 1.0 {
		return true
	}
	if p.sampleRate <= 0.0 {
		return false
	}
	h := fnv.New32a()
	_, _ = h.Write([]byte(requestID))
	return float64(h.Sum32()%10000)/10000.0 < p.sampleRate
}
