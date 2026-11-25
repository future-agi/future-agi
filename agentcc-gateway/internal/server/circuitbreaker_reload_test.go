package server

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ReloadRouting deliberately replaces the router and breaker registry. A
// delayed callback from the old registry must not readmit a newly tripped
// provider in the replacement, whose configured cooldown may be different.
func TestCircuitBreaker_ReloadKeepsOldReadmissionOnOldRouter(t *testing.T) {
	var calls atomic.Int64
	upstream := adaptiveUpstream(t, false, 0, &calls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{
			"adaptive-sample": {{Provider: "fast"}},
		},
		CircuitBreaker: config.CircuitBreakerConfig{
			Enabled: true, FailureThreshold: 1, Cooldown: 100 * time.Millisecond,
		},
		Failover: config.FailoverConfig{Enabled: true, MaxAttempts: 1},
	}
	srv, _ := adaptiveServer(t, cfg)
	oldRouter := srv.registry.Router()
	oldRegistry := srv.handlers.healthMonitor.CircuitBreakerRegistry()
	oldBreaker := oldRegistry.Get("fast")
	lateGeneration, ok := oldBreaker.Allow()
	if !ok {
		t.Fatal("closed breaker refused in-flight request")
	}
	openReconciliationBreaker(t, oldBreaker)
	time.Sleep(60 * time.Millisecond)
	oldBreaker.RecordFailure(lateGeneration, errors.New("synthetic late failure before reload"))

	cfg.Routing.CircuitBreaker.Cooldown = time.Second
	if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
		t.Fatal(err)
	}
	newRouter := srv.registry.Router()
	newRegistry := srv.handlers.healthMonitor.CircuitBreakerRegistry()
	if newRouter == oldRouter || newRegistry == oldRegistry {
		t.Fatal("reload must replace the router and registry as on dev")
	}
	newBreaker := newRegistry.Get("fast")
	openReconciliationBreaker(t, newBreaker)

	deadline := time.Now().Add(500 * time.Millisecond)
	for {
		if _, err := oldRouter.Select("adaptive-sample"); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("old router was never readmitted after its extended cooldown")
		}
		time.Sleep(time.Millisecond)
	}
	if _, allowed := oldBreaker.Allow(); !allowed {
		t.Fatal("old router was readmitted before its late-failure cooldown ended")
	}
	if _, err := newRouter.Select("adaptive-sample"); err == nil {
		t.Fatal("old callback changed the replacement router's health")
	}
	if _, allowed := newBreaker.Allow(); allowed {
		t.Fatal("replacement breaker did not retain its longer cooldown")
	}
}
