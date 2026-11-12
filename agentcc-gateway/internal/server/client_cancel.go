package server

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// clientCancelled reports whether the client ended the request: its context
// was cancelled, not ended by a time limit. Such a request says nothing about
// its provider, so it counts toward none of the provider's shared state, as
// neither a failure nor a success: not its health, not its latency, not its
// circuit breaker (routing.Failover leaves the breaker alone the same way).
func clientCancelled(ctx context.Context) bool {
	return errors.Is(ctx.Err(), context.Canceled)
}

// recordProviderSignals records the request's provider latency and health
// for routing, after the response, unless its client cancelled it.
func (h *Handlers) recordProviderSignals(ctx context.Context, rc *models.RequestContext) {
	if clientCancelled(ctx) {
		return
	}
	if router := h.registry.Router(); router != nil {
		if d, ok := rc.Timings["provider"]; ok {
			router.RecordLatency(rc.Provider, d)
		}
	}
	h.recordProviderHealth(rc)
}
