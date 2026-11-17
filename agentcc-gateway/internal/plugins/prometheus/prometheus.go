package prometheus

import (
	"context"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
)

// Plugin is a pipeline plugin that records per-request metrics into a Prometheus registry.
type Plugin struct {
	registry *metrics.Registry
	enabled  bool
}

// New creates a new Prometheus metrics plugin. An enabled plugin also
// exports the upstream slots of every provider entry (ProviderSaturation).
func New(registry *metrics.Registry, enabled bool) *Plugin {
	if enabled && registry != nil {
		registry.SetCollector("provider_saturation", ProviderSaturation)
	}
	return &Plugin{
		registry: registry,
		enabled:  enabled,
	}
}

// Provider saturation metrics, one series per provider entry (label
// provider): a config.yaml provider name or an org instance's ID.
const (
	MetricProviderInFlight      = "agentcc_provider_in_flight"
	MetricProviderMaxConcurrent = "agentcc_provider_max_concurrent"
	MetricProviderQueued        = "agentcc_provider_queued"
	MetricProviderSaturated     = "agentcc_provider_saturated"
	MetricProviderBusyTotal     = "agentcc_provider_busy_total"
	MetricProviderQueueWait     = "agentcc_provider_queue_wait_ms"
)

// ProviderSaturation is the upstream slots of every provider entry
// (admission.Snapshot): in-flight calls, max_concurrent and calls waiting
// for a slot, summed over the entry's live instances, whether it is
// saturated, busy refusals since start, and the wait for a slot.
func ProviderSaturation() []metrics.Family {
	snapshot := admission.Snapshot()
	inFlight := metrics.Family{Name: MetricProviderInFlight, Type: "gauge",
		Help: "Upstream calls in flight per provider entry"}
	maxConcurrent := metrics.Family{Name: MetricProviderMaxConcurrent, Type: "gauge",
		Help: "Upstream slots (max_concurrent) per provider entry"}
	queued := metrics.Family{Name: MetricProviderQueued, Type: "gauge",
		Help: "Calls waiting for an upstream slot per provider entry"}
	saturated := metrics.Family{Name: MetricProviderSaturated, Type: "gauge",
		Help: "1 while a provider entry is saturated: a call waits, every slot is taken, or a call was refused busy in the last 10s"}
	busy := metrics.Family{Name: MetricProviderBusyTotal, Type: "counter",
		Help: "Calls refused provider_busy (no slot within queue_timeout) per provider entry"}
	wait := metrics.Family{Name: MetricProviderQueueWait, Type: "histogram",
		Help: "Wait for an upstream slot in milliseconds per provider entry, refused calls included"}
	for _, st := range snapshot {
		lbl := map[string]string{"provider": st.Entry}
		if st.Live {
			sat := 0.0
			if st.Saturated {
				sat = 1
			}
			inFlight.Samples = append(inFlight.Samples, metrics.Sample{Labels: lbl, Value: float64(st.InFlight)})
			maxConcurrent.Samples = append(maxConcurrent.Samples, metrics.Sample{Labels: lbl, Value: float64(st.MaxConcurrent)})
			queued.Samples = append(queued.Samples, metrics.Sample{Labels: lbl, Value: float64(st.Queued)})
			saturated.Samples = append(saturated.Samples, metrics.Sample{Labels: lbl, Value: sat})
		}
		busy.Samples = append(busy.Samples, metrics.Sample{Labels: lbl, Value: float64(st.BusyRefusals)})
		for i, bound := range st.Wait.BoundsMs {
			wait.Samples = append(wait.Samples, metrics.Sample{Suffix: "_bucket",
				Labels: map[string]string{"provider": st.Entry, "le": strconv.FormatFloat(bound, 'f', -1, 64)},
				Value:  float64(st.Wait.Cumulative[i])})
		}
		wait.Samples = append(wait.Samples,
			metrics.Sample{Suffix: "_bucket", Labels: map[string]string{"provider": st.Entry, "le": "+Inf"}, Value: float64(st.Wait.Count)},
			metrics.Sample{Suffix: "_sum", Labels: lbl, Value: st.Wait.SumMs},
			metrics.Sample{Suffix: "_count", Labels: lbl, Value: float64(st.Wait.Count)})
	}
	return []metrics.Family{inFlight, maxConcurrent, queued, saturated, busy, wait}
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

	// Token usage.
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := rc.Response.Usage
		if usage.PromptTokens > 0 {
			p.registry.CounterAdd("agentcc_tokens_input_total", "Total input tokens", modelProviderLabels, int64(usage.PromptTokens))
		}
		if usage.CompletionTokens > 0 {
			p.registry.CounterAdd("agentcc_tokens_output_total", "Total output tokens", modelProviderLabels, int64(usage.CompletionTokens))
		}
	}

	// Cost.
	if costStr, ok := rc.Metadata["cost"]; ok {
		if cost, err := strconv.ParseFloat(costStr, 64); err == nil && cost > 0 {
			// Store as microdollars for integer precision.
			microdollars := int64(cost * 1_000_000)
			p.registry.CounterAdd("agentcc_cost_microdollars_total", "Total cost in microdollars", modelProviderLabels, microdollars)
		}
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
