package prometheus

import (
	"context"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin is a pipeline plugin that records per-request metrics into a Prometheus registry.
type Plugin struct {
	registry *metrics.Registry
	enabled  bool
}

// New creates a new Prometheus metrics plugin.
func New(registry *metrics.Registry, enabled bool) *Plugin {
	return &Plugin{
		registry: registry,
		enabled:  enabled,
	}
}

func (p *Plugin) Name() string         { return "prometheus" }
func (p *Plugin) Priority() int        { return 998 }
func (p *Plugin) IsPostParallel() bool { return true } // Counter updates, safe to parallelize.

// ProcessRequest is a no-op for the Prometheus plugin.
func (p *Plugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// ProcessResponse records per-request metrics into the registry.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.registry == nil {
		return pipeline.ResultContinue()
	}

	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	provider := rc.Provider

	// Determine status.
	status := "ok"
	if len(rc.Errors) > 0 {
		status = "error"
	}

	requestLabels := map[string]string{
		"model":    model,
		"provider": provider,
		"status":   status,
	}
	modelProviderLabels := map[string]string{
		"model":    model,
		"provider": provider,
	}

	// Request count.
	p.registry.CounterInc("agentcc_requests_total", "Total requests processed", requestLabels)

	// Error count.
	if status == "error" {
		p.registry.CounterInc("agentcc_errors_total", "Total errors", modelProviderLabels)
	}

	// One request record, but tokens and cost retain the actual serving model
	// for every attempt, including blocked candidates and transport fallbacks.
	recordUsage := func(labels map[string]string, usage *models.Usage, costStr string) {
		if usage != nil {
			if usage.PromptTokens > 0 {
				p.registry.CounterAdd("agentcc_tokens_input_total", "Total input tokens", labels, int64(usage.PromptTokens))
			}
			if usage.CompletionTokens > 0 {
				p.registry.CounterAdd("agentcc_tokens_output_total", "Total output tokens", labels, int64(usage.CompletionTokens))
			}
		}
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil && cost > 0 {
			p.registry.CounterAdd("agentcc_cost_microdollars_total", "Total cost in microdollars", labels, int64(cost*1_000_000))
		}
	}
	if rc.Attempts != nil && len(rc.Attempts.Attempts) > 0 {
		for _, a := range rc.Attempts.Attempts {
			recordUsage(map[string]string{"model": a.Model, "provider": a.Provider}, a.Usage, a.Cost)
		}
	} else {
		recordUsage(modelProviderLabels, rc.ResponseUsage(), rc.Metadata["cost"])
	}

	// Cache status.
	if cacheStatus, ok := rc.Metadata["cache_status"]; ok {
		if strings.HasPrefix(cacheStatus, "hit") {
			cacheType := "exact"
			if cacheStatus == "hit_semantic" {
				cacheType = "semantic"
			}
			p.registry.CounterInc("agentcc_cache_hits_total", "Total cache hits", map[string]string{"type": cacheType})
		} else if cacheStatus == "miss" {
			p.registry.CounterInc("agentcc_cache_misses_total", "Total cache misses", nil)
		}
	}

	// Request duration histogram.
	durationMs := float64(rc.Elapsed().Milliseconds())
	p.registry.HistogramObserve("agentcc_request_duration_ms", "Request duration in milliseconds", modelProviderLabels, durationMs)

	return pipeline.ResultContinue()
}
