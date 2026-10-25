package routing

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestExecuteOpensOnlyTheFailingModelsCircuit is the Failover.Execute case
// of scripts/scenarios/gates/breaker_per_model.sh: 25 failures through
// Execute on one managed model open that model's circuits, and no other,
// because failover.go checks and records under BreakerKey and BreakerKey
// keys each model separately.
func TestExecuteOpensOnlyTheFailingModelsCircuit(t *testing.T) {
	const threshold = 5
	providers := []string{"managed-a", "managed-b"}
	pids := map[string]bool{}
	var targets []config.RoutingTargetConfig
	for i, p := range providers {
		pids[p] = true
		targets = append(targets, config.RoutingTargetConfig{Provider: p, Priority: i, Weight: 1})
	}
	router, err := NewRouter(config.RoutingConfig{
		DefaultStrategy: "cost-optimized", // no prices: ranks by priority
		Targets:         map[string][]config.RoutingTargetConfig{"m-fail": targets, "m-ok": targets},
	}, pids, nil)
	if err != nil {
		t.Fatal(err)
	}
	cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{
		Enabled: true, FailureThreshold: threshold, Cooldown: time.Hour,
	}, nil)
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: len(providers)}, router, nil, cbReg)

	calls := map[string]int{} // "provider model" -> upstream calls
	// callFor is the request path's call for model: the call's model
	// argument is a target's override, empty here, so it closes over model.
	callFor := func(model string) FailoverCallFunc {
		return func(_ context.Context, providerID, _ string) error {
			calls[providerID+" "+model]++
			if model == "m-fail" {
				return &models.APIError{Status: http.StatusInternalServerError, Type: models.ErrTypeServer, Code: "upstream_error", Message: "boom"}
			}
			return nil
		}
	}

	for i := 0; i < 25; i++ {
		if res, err := f.Execute(context.Background(), "m-fail", callFor("m-fail")); res != nil || err == nil {
			t.Fatalf("m-fail request %d: Execute = (%v, %v), want an error", i, res, err)
		}
	}
	for _, p := range providers {
		if got := calls[p+" m-fail"]; got != threshold {
			t.Errorf("%s m-fail upstream calls = %d, want %d (the circuit opens after %d failures)", p, got, threshold, threshold)
		}
		if !cbReg.Refuses(p, "m-fail") || cbReg.Get(BreakerKey(p, "m-fail", "")).State() != StateOpen {
			t.Errorf("%s|m-fail circuit is not open after 25 failures", p)
		}
		if cbReg.Refuses(p, "m-ok") {
			t.Errorf("%s|m-ok circuit refuses: one failing model opened another model's circuit", p)
		}
	}

	res, err := f.Execute(context.Background(), "m-ok", callFor("m-ok"))
	if err != nil || res == nil || res.ProviderID != providers[0] || res.Attempts != 1 {
		t.Fatalf("m-ok: Execute = (%+v, %v), want a first-attempt success on %s", res, err, providers[0])
	}
	// With a closed m-ok circuit beside the open m-fail one, the provider
	// view is half-open, not open (sc/18_breaker.sh check 4).
	if got := cbReg.Get(providers[0]).State(); got != StateHalfOpen {
		t.Errorf("%s provider view = %v, want half-open: one failing model must not open the provider", providers[0], got)
	}

	before := len(calls)
	before0 := calls[providers[0]+" m-fail"] + calls[providers[1]+" m-fail"]
	_, err = f.Execute(context.Background(), "m-fail", callFor("m-fail"))
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable {
		t.Fatalf("m-fail after its circuits opened: err = %v, want a 503", err)
	}
	if len(calls) != before || calls[providers[0]+" m-fail"]+calls[providers[1]+" m-fail"] != before0 {
		t.Errorf("m-fail reached the upstream with its circuits open: calls %v", calls)
	}
}
