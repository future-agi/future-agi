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
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin is a pipeline plugin that creates OTel-compatible trace spans and records metrics.
type Plugin struct {
	exporter    otelpkg.SpanExporter
	metrics     *otelpkg.Metrics
	sampleRate  float64
	enabled     bool
	serviceName string
	attributes  map[string]string

	// Body capture: off unless configured, and redacted through the same
	// per-org privacy config the request log uses.
	includeBodies bool
	// tracePropagation honours an inbound W3C traceparent (see config).
	tracePropagation bool
	// metadataAttributes flattens caller metadata into its own attributes.
	metadataAttributes bool
	// captureHeaders is the compiled request-header allowlist; nil captures none.
	captureHeaders *headerMatcher
	// bodyAttributes exports the body's unknown top-level fields.
	bodyAttributes bool
	redactor       *privacy.Redactor
	tenantStore    *tenant.Store

	// spans stores in-flight spans keyed by request ID.
	spans sync.Map
}

// New creates an OTel plugin from config.
func New(cfg config.OTelConfig) *Plugin {
	var exp otelpkg.SpanExporter
	switch strings.ToLower(cfg.Exporter) {
	case "stdout", "":
		exp = otelpkg.NewStdoutExporter(os.Stdout)
	case "otlp":
		otlp, err := otelpkg.NewOTLPExporter(otelpkg.OTLPOptions{
			Endpoint:    cfg.Endpoint,
			ServiceName: cfg.ServiceName,
			Resource:    cfg.Attributes,
			Headers:     cfg.Headers,
		})
		if err != nil {
			// Config.Validate rejects a bad endpoint at startup, so reaching
			// here means the gateway would otherwise trace into a void.
			slog.Error("otlp exporter unavailable, falling back to stdout",
				"error", err, "endpoint", cfg.Endpoint)
			exp = otelpkg.NewStdoutExporter(os.Stdout)
			break
		}
		slog.Info("otlp trace export enabled",
			"endpoint", cfg.Endpoint, "headers", len(cfg.Headers))
		exp = otlp
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

	if cfg.Enabled && cfg.IncludeBodies {
		slog.Warn("otel body capture is enabled — prompts and completions will be sent to the trace collector")
	}
	if cfg.Enabled && len(cfg.CaptureHeaders) > 0 {
		slog.Warn("otel request-header capture is enabled — matching headers will be sent to the trace collector",
			"patterns", cfg.CaptureHeaders)
	}

	return &Plugin{
		exporter:         exp,
		metrics:          &otelpkg.Metrics{},
		sampleRate:       sampleRate,
		enabled:          cfg.Enabled,
		serviceName:      serviceName,
		attributes:       cfg.Attributes,
		includeBodies:    cfg.Enabled && cfg.IncludeBodies,
		tracePropagation: cfg.TracePropagation,
		// Unset means on — the flattened copies are additive.
		metadataAttributes: cfg.MetadataAttributes == nil || *cfg.MetadataAttributes,
		bodyAttributes:     cfg.BodyAttributes == nil || *cfg.BodyAttributes,
		captureHeaders:     newHeaderMatcher(cfg.CaptureHeaders),
	}
}

// SetRedactor sets the gateway-wide redactor, used when an org has no privacy
// config of its own.
func (p *Plugin) SetRedactor(r *privacy.Redactor) { p.redactor = r }

// SetTenantStore supplies per-org privacy configuration.
func (p *Plugin) SetTenantStore(s *tenant.Store) { p.tenantStore = s }

// NewWithExporter creates a plugin with a specific exporter (for testing).
func NewWithExporter(exp otelpkg.SpanExporter, sampleRate float64, enabled bool) *Plugin {
	return &Plugin{
		exporter:           exp,
		metrics:            &otelpkg.Metrics{},
		sampleRate:         sampleRate,
		enabled:            enabled,
		serviceName:        "agentcc-gateway",
		metadataAttributes: true,
		bodyAttributes:     true,
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

	// Nest under the caller's span rather than starting a second root.
	if p.tracePropagation {
		if traceID, parentID, ok := parseTraceparent(rc.RequestHeaders.Get("traceparent")); ok {
			// agentcc.trace_id is normally derived from span.TraceID and is what
			// joins a span to its request-log row, so pin the gateway's own id
			// before the caller's overwrites it.
			if rc.TraceID != "" {
				span.SetAttribute("agentcc.trace_id", rc.TraceID)
			}
			span.TraceID = traceID
			span.ParentID = parentID
		}
	}

	// Classification. Without span.kind the platform files every span as
	// UNKNOWN, so this is not optional.
	kind, operation := classifyEndpoint(rc.EndpointType)
	span.SetAttribute("gen_ai.span.kind", kind)
	span.SetAttribute("gen_ai.operation.name", operation)

	span.SetAttribute("gen_ai.request.model", rc.Model)
	span.SetAttribute("agentcc.request_id", rc.RequestID)
	span.SetAttribute("agentcc.is_stream", rc.IsStream)

	if rc.UserID != "" {
		span.SetAttribute("user.id", rc.UserID)
	}
	if rc.SessionID != "" {
		span.SetAttribute("session.id", rc.SessionID)
	}

	if rc.Request != nil && rc.Request.MaxTokens != nil {
		span.SetAttribute("gen_ai.request.max_tokens", *rc.Request.MaxTokens)
	}

	// Caller-supplied dimensions (profile, tenant, application...) and, when
	// configured, allowlisted request headers.
	p.attachMetadata(span, rc)
	p.attachBodyExtras(span, rc)
	p.attachRequestHeaders(span, rc)

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

	// Provider / resolved model. gen_ai.system is an accepted alias for the
	// provider, so it must name the upstream, not this gateway — the gateway's
	// own identity is the resource service.name.
	if rc.Provider != "" {
		span.SetAttribute("gen_ai.provider.name", rc.Provider)
		span.SetAttribute("gen_ai.system", rc.Provider)
	}
	if rc.ResolvedModel != "" {
		span.SetAttribute("gen_ai.response.model", rc.ResolvedModel)
	}

	// Token usage.
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := rc.Response.Usage
		span.SetAttribute("gen_ai.usage.input_tokens", usage.PromptTokens)
		span.SetAttribute("gen_ai.usage.output_tokens", usage.CompletionTokens)
		span.SetAttribute("gen_ai.usage.total_tokens", usage.PromptTokens+usage.CompletionTokens)
		p.metrics.InputTokens.Add(int64(usage.PromptTokens))
		p.metrics.OutputTokens.Add(int64(usage.CompletionTokens))
	}

	// Cost from metadata (set by cost plugin).
	if costStr, ok := rc.Metadata["cost"]; ok {
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil {
			// Honoured ahead of the platform's own token-based estimate, which
			// cannot price Azure deployment names correctly.
			span.SetAttribute("gen_ai.cost.total", cost)
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

	// TTFT from timings. Seconds, per the convention.
	if ttft, ok := rc.Timings["ttft"]; ok {
		span.SetAttribute("gen_ai.server.time_to_first_token", ttft.Seconds())
	}

	// Total duration, in seconds per the convention.
	span.SetAttribute("gen_ai.client.operation.duration", rc.Elapsed().Seconds())

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

	// Dimensions for non-chat endpoints. Unlike bodies these are small and
	// carry no user content beyond what the request already declares, so they
	// are not gated on include_bodies.
	p.attachEndpointAttributes(span, rc)

	if p.includeBodies {
		p.attachBodies(span, rc)
	}

	span.End()

	// Export span.
	if err := p.exporter.Export([]*otelpkg.Span{span}); err != nil {
		slog.Warn("otel export error", "error", err, "request_id", rc.RequestID)
	}

	return pipeline.ResultContinue()
}

// endpointKinds maps the gateway's endpoint types onto the platform's span
// kinds. Anything unlisted is a model call, which is what the gateway mostly
// proxies; only retrieval-shaped and embedding endpoints differ.
var endpointKinds = map[string]struct{ kind, operation string }{
	"embedding":     {"embedding", "embeddings"},
	"genai_embed":   {"embedding", "embeddings"},
	"rerank":        {"reranker", "rerank"},
	"search":        {"retriever", "search"},
	"vector_stores": {"retriever", "search"},
	"completion":    {"llm", "text_completion"},
}

// classifyEndpoint returns the span kind and operation name for an endpoint
// type. Both are required: span kind is how the platform files the span, and
// without it every gateway span is filed as UNKNOWN.
func classifyEndpoint(endpointType string) (kind, operation string) {
	if e, ok := endpointKinds[endpointType]; ok {
		return e.kind, e.operation
	}
	switch endpointType {
	case "", "chat", "responses", "assistants", "anthropic_messages", "genai_generate", "genai_stream":
		return "llm", "chat"
	default:
		// Image, speech, transcription, translation, OCR, video: still model
		// calls, distinguished by operation rather than kind.
		return "llm", endpointType
	}
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

// parseTraceparent extracts the trace id and span id from a W3C `traceparent`
// header, per https://www.w3.org/TR/trace-context/.
//
//	00-<32 hex trace id>-<16 hex span id>-<2 hex flags>
//
// It reports ok only for a sampled (flags bit 0 set) version-00 header with
// non-zero ids. An unsampled caller never exports the span it names, so
// parenting to it would leave a permanently dangling reference.
func parseTraceparent(h string) (traceID, parentID string, ok bool) {
	parts := strings.Split(strings.TrimSpace(h), "-")
	if len(parts) != 4 {
		return "", "", false
	}
	version, tid, sid, flags := parts[0], parts[1], parts[2], parts[3]

	// Only version 00 is defined. "ff" is explicitly invalid.
	if version != "00" {
		return "", "", false
	}
	if !isHex(tid, 32) || !isHex(sid, 16) {
		return "", "", false
	}
	if strings.Trim(tid, "0") == "" || strings.Trim(sid, "0") == "" {
		return "", "", false
	}
	if !isHex(flags, 2) {
		return "", "", false
	}
	f, err := strconv.ParseUint(flags, 16, 8)
	if err != nil || f&0x01 == 0 {
		return "", "", false
	}
	return tid, sid, true
}

func isHex(s string, n int) bool {
	if len(s) != n {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}
