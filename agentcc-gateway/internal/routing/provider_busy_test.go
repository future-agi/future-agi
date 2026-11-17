package routing

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// PROVIDER-CONCURRENCY-1: a provider_busy refusal is the gateway's own
// admission before dispatch. It is classified from the gateway's side, never
// retried on the entry that refused it, never counted against the provider's
// circuit, and still fails over to another target.
func TestProviderBusyRouting(t *testing.T) {
	busy := fmt.Errorf("wrapped: %w", models.ErrProviderBusy("entry"))

	if class, origin := ClassifyError(busy, 0); class != models.ErrClassUpstream5xx || origin != models.OriginGateway {
		t.Errorf("class %s origin %s; want upstream_5xx from the gateway", class, origin)
	}
	if reason := AttemptReason(busy); reason != models.ReasonStatus5xx {
		t.Errorf("next attempt's reason %q; want the 503's status_5xx", reason)
	}
	if outcome, _, _ := AttemptResult(context.Background(), busy); outcome != models.OutcomeError {
		t.Errorf("outcome %s", outcome)
	}
	if AttemptStatus(busy) != 503 {
		t.Errorf("status %d", AttemptStatus(busy))
	}

	r := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{503}})
	if r.ShouldRetry(busy) {
		t.Error("a busy entry is retried in place")
	}
	if !r.ShouldRetry(models.ErrServiceUnavailable("down")) {
		t.Error("another 503 is no longer retried")
	}
	calls := 0
	if _, err := r.Execute(context.Background(), func() error { calls++; return busy }); calls != 1 || !models.IsProviderBusy(err) {
		t.Errorf("Execute made %d calls, err %v; want one call", calls, err)
	}

	fo := NewFailover(config.FailoverConfig{Enabled: true}, nil, nil, nil)
	if !fo.ShouldFailover(busy) {
		t.Error("a busy entry no longer fails over (default 503)")
	}

	cb := NewCircuitBreaker("entry", config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, OnStatusCodes: []int{503}}, nil)
	for i := 0; i < 3; i++ {
		cb.RecordFailure(busy)
	}
	if cb.State() != StateClosed {
		t.Fatalf("busy refusals opened the circuit (%v)", cb.State())
	}
	cb.RecordFailure(models.ErrServiceUnavailable("down"))
	if cb.State() != StateOpen {
		t.Fatal("a real 503 no longer opens the circuit")
	}
}

// TestProviderBusyNeedsTheGatewaysOwnRefusal: an upstream's error that only
// carries a provider_busy code is not the gateway's refusal.
func TestProviderBusyNeedsTheGatewaysOwnRefusal(t *testing.T) {
	upstream := models.ErrUpstreamProvider(503, "busy upstream")
	upstream.Code = models.CodeProviderBusy
	if models.IsProviderBusy(upstream) || models.IsProviderBusy(errors.New("provider_busy")) || models.IsProviderBusy(nil) {
		t.Fatal("an error the gateway did not make reads as its busy refusal")
	}
}
