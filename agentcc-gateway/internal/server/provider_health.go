package server

import (
	"context"
	"errors"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// recordProviderOutcome samples only the operator's actual upstream instance.
// Org instances can share its routing ID but have different credentials/endpoints,
// so their failures (a revoked org key, say) never reach shared health or weights.
// Cache hits, policy refusals and client cancellations are not upstream outcomes.
// Latency is recorded for successful calls only: a call that fails fast says
// nothing about how fast the provider serves, and must not look attractive.
func (h *Handlers) recordProviderOutcome(ctx context.Context, provider providers.Provider, elapsed time.Duration, err error) {
	// Adapters may normalize cancellation to an API timeout without an Unwrap.
	if h.healthMonitor == nil || h.registry == nil || errors.Is(ctx.Err(), context.Canceled) || errors.Is(err, context.Canceled) {
		return
	}
	base, ok := h.registry.GetProvider(provider.ID())
	if !ok || base != provider {
		return
	}
	if err != nil {
		h.healthMonitor.RecordError(provider.ID(), err)
		return
	}
	h.healthMonitor.RecordLatency(provider.ID(), elapsed)
	h.healthMonitor.RecordSuccess(provider.ID())
}

func (h *Handlers) callChatProvider(ctx context.Context, provider providers.Provider, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	start := time.Now()
	resp, err := provider.ChatCompletion(ctx, req)
	h.recordProviderOutcome(ctx, provider, time.Since(start), err)
	return resp, err
}
