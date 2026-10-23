package server

import (
	"net/http"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func (h *Handlers) buildStreamingMetadataChunk(rc *models.RequestContext, streamID string, streamCreated int64, usage *models.Usage) *models.StreamChunk {
	model := rc.ResolvedModel
	if model == "" && rc.Request != nil {
		model = rc.Request.Model
	}
	if streamID == "" {
		streamID = rc.RequestID
	}
	if streamCreated == 0 {
		streamCreated = time.Now().Unix()
	}

	var cost float64
	if rawCost, ok := rc.Metadata["cost"]; ok {
		if parsed, err := strconv.ParseFloat(rawCost, 64); err == nil {
			cost = parsed
		}
	}

	chunk := &models.StreamChunk{
		ID:      streamID,
		Object:  "chat.completion.chunk",
		Created: streamCreated,
		Model:   model,
		Choices: []models.StreamChoice{},
		Usage:   billedUsage(rc, usage),
	}
	if wantsStreamMetadata(rc) {
		chunk.AgentccMetadata = &models.AgentccStreamMetadata{
			Cost:      cost,
			LatencyMs: rc.Elapsed().Milliseconds(),
		}
	}
	return chunk
}

// wantsStreamMetadata reports whether the client asked for the gateway's
// metadata on a stream's final chunk (x-agentcc-include-metadata: 1 or true).
func wantsStreamMetadata(rc *models.RequestContext) bool {
	include := rc.RequestHeaders.Get("x-agentcc-include-metadata")
	return include == "1" || strings.EqualFold(include, "true")
}

// wantsFinalStreamChunk reports whether a stream ends with the gateway's
// final chunk, the one chunk with empty choices a client may get (OpenAI's
// streaming contract): only when the client asked for usage
// (stream_options.include_usage) or for the gateway's metadata, on chat and
// on /v1/completions. Without either, every chunk the client gets has a
// choice, so the usual SDK loop over choices[0] reaches [DONE], and the
// usage rides on the finish chunk (finishHold).
func wantsFinalStreamChunk(rc *models.RequestContext) bool {
	return rc.Flags.ClientIncludeUsage || wantsStreamMetadata(rc)
}

// clientGetsStreamChunk reports whether a chunk from the upstream goes to the
// client. A chunk with no choice (the upstream's usage chunk, which the
// gateway asks for on every chat stream, Azure's prompt_filter_results
// chunk, or another chunk with empty or null choices) does not: its usage
// is metered, and the client gets it once, on the gateway's final chunk when
// it asked for usage, else on the finish chunk (finishHold).
func clientGetsStreamChunk(chunk models.StreamChunk) bool {
	return len(chunk.Choices) > 0
}

// setAgentccHeaders writes the gateway's response headers (call-order step
// 19), then every header set with rc.SetResponseHeader. The provider
// whose client timeout narrows x-agentcc-timeout-ms is the one rc names
// (servingProvider).
func (h *Handlers) setAgentccHeaders(w http.ResponseWriter, rc *models.RequestContext) {
	provider, _ := h.servingProvider(rc)
	h.setAgentccHeadersForProvider(w, rc, provider)
}

// setAgentccHeadersForProvider is setAgentccHeaders for provider, the
// instance that served. Chat retries can switch between base and org
// instances with the same ID, so chat passes the successful instance rather
// than reconstructing it from routing metadata.
func (h *Handlers) setAgentccHeadersForProvider(w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider) {
	h.observeHook(hookSetAgentccHeaders, rc)
	w.Header().Set("x-agentcc-provider", rc.Provider)
	if rc.ResolvedModel != "" {
		w.Header().Set("x-agentcc-model-used", rc.ResolvedModel)
	}

	latency := rc.Elapsed()
	w.Header().Set("x-agentcc-latency-ms", latencyString(latency))

	if cost, ok := rc.Metadata["cost"]; ok {
		w.Header().Set("x-agentcc-cost", cost)
	}
	if status, ok := rc.Metadata["cache_status"]; ok && status != "" {
		w.Header().Set("x-agentcc-cache", status)
	} else if rc.Flags.CacheHit {
		w.Header().Set("x-agentcc-cache", "hit")
	}
	if rc.Flags.GuardrailTriggered {
		w.Header().Set("x-agentcc-guardrail-triggered", "true")
	}
	setGuardrailOutcomeHeaders(w.Header(), rc)
	if rc.Flags.FallbackUsed {
		w.Header().Set("x-agentcc-fallback-used", "true")
	}
	if strategy, ok := rc.Metadata["routing_strategy"]; ok {
		w.Header().Set("x-agentcc-routing-strategy", strategy)
	}
	if timeout, ok := rc.Metadata["timeout_ms"]; ok {
		w.Header().Set("x-agentcc-timeout-ms", appliedTimeoutMs(provider, timeout))
	}

	// Advanced routing headers.
	if _, ok := rc.Metadata["provider_locked"]; ok {
		w.Header().Set("x-agentcc-provider-locked", rc.Metadata["provider_lock_target"])
	}
	if tier, ok := rc.Metadata["complexity_tier"]; ok {
		w.Header().Set("x-agentcc-complexity-tier", tier)
	}
	if winner, ok := rc.Metadata["race_winner"]; ok {
		w.Header().Set("x-agentcc-race-winner", winner)
	}

	// Managed models and auto routing.
	if v := rc.Metadata["billing_mode"]; v != "" {
		w.Header().Set("x-agentcc-billing-mode", v)
	}
	if v, ok := rc.Metadata["managed_credits_remaining"]; ok {
		w.Header().Set("x-agentcc-managed-credits-remaining", v)
	}
	if v, ok := rc.Metadata["auto_model"]; ok {
		w.Header().Set("x-agentcc-auto-model", v)
		w.Header().Set("x-agentcc-auto-tier", rc.Metadata["auto_task_tier"])
		w.Header().Set("x-agentcc-auto-reason", rc.Metadata["auto_reason"])
	}

	// Credit headers for managed keys.
	if v, ok := rc.Metadata["credits_used"]; ok {
		w.Header().Set("x-agentcc-credits-used", v)
	}
	if v, ok := rc.Metadata["credits_remaining"]; ok {
		w.Header().Set("x-agentcc-credits-remaining", v)
	}

	// Rate limit headers.
	if v, ok := rc.Metadata["ratelimit_limit"]; ok {
		w.Header().Set("x-ratelimit-limit-requests", v)
	}
	if v, ok := rc.Metadata["ratelimit_remaining"]; ok {
		w.Header().Set("x-ratelimit-remaining-requests", v)
	}
	if v, ok := rc.Metadata["ratelimit_reset"]; ok {
		w.Header().Set("x-ratelimit-reset-requests", v)
	}

	rc.ApplyResponseHeaders(w.Header())
}

// Guardrail outcome headers: on an answer a check triggered on
// without blocking it, the strongest action taken (warn, else log) and the
// names of the checks that triggered.
const (
	headerGuardrailAction     = "X-Agentcc-Guardrail-Action"
	headerGuardrailsTriggered = "X-Agentcc-Guardrails-Triggered"
)

// setGuardrailOutcomeHeaders writes the guardrail outcome headers from
// rc.GuardrailResults: x-agentcc-guardrail-action is warn when any check
// warned, else log (a check that logged, or one that rewrote the request
// and let it through); x-agentcc-guardrails-triggered is the checks' names,
// sorted, each once, comma-separated. A block writes neither: the client
// gets the 403 guardrail_error, which names the check. With no result it
// does nothing and allocates nothing.
func setGuardrailOutcomeHeaders(h http.Header, rc *models.RequestContext) {
	if len(rc.GuardrailResults) == 0 {
		return
	}
	action := "log"
	names := make([]string, 0, len(rc.GuardrailResults))
	for _, r := range rc.GuardrailResults {
		switch r.Action {
		case "block":
			return
		case "warn":
			action = "warn"
		}
		if r.Name != "" {
			names = append(names, r.Name)
		}
	}
	slices.Sort(names)
	names = slices.Compact(names)
	h[headerGuardrailAction] = []string{action}
	h[headerGuardrailsTriggered] = []string{strings.Join(names, ",")}
}

func latencyString(d time.Duration) string {
	return strconv.FormatInt(d.Milliseconds(), 10)
}
