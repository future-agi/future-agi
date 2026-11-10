package routing

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func admitProbe(t *testing.T, cb *CircuitBreaker) uint64 {
	t.Helper()
	generation, allowed := cb.Allow()
	if !allowed {
		t.Fatal("request unexpectedly rejected")
	}
	return generation
}

func expireProbeCooldown(cb *CircuitBreaker) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.lastFailure = time.Now().Add(-cb.cfg.Cooldown)
}

func tripProbeCircuit(t *testing.T, cb *CircuitBreaker) {
	t.Helper()
	for i := 0; i < cb.cfg.FailureThreshold; i++ {
		cb.RecordFailure(admitProbe(t, cb), serverErr())
	}
	if cb.State() != StateOpen {
		t.Fatal("circuit did not open")
	}
	expireProbeCooldown(cb)
}

func TestCircuitBreaker_StaleResultsDoNotReleaseProbe(t *testing.T) {
	for _, origin := range []string{"closed", "half-open"} {
		for _, outcome := range []struct {
			name string
			err  error
		}{{"success", nil}, {"client-error", clientErr()}, {"server-error", serverErr()}} {
			t.Run(origin+"/"+outcome.name, func(t *testing.T) {
				cfg := newTestCBConfig()
				cfg.HalfOpenMaxProbes = 2
				cb := NewCircuitBreaker("synthetic", cfg, nil)
				if origin == "half-open" {
					tripProbeCircuit(t, cb)
				}
				stale := admitProbe(t, cb)
				if origin == "closed" {
					tripProbeCircuit(t, cb)
				} else {
					cb.RecordFailure(admitProbe(t, cb), serverErr())
					expireProbeCooldown(cb)
				}

				current := admitProbe(t, cb)
				admitProbe(t, cb)
				if stale == current {
					t.Fatal("recovery cycle reused the previous admission generation")
				}
				if outcome.err == nil {
					cb.RecordSuccess(stale)
				} else {
					cb.RecordFailure(stale, outcome.err)
				}
				if cb.State() != StateHalfOpen {
					t.Fatal("stale result changed the current recovery state")
				}
				if _, allowed := cb.Allow(); allowed {
					t.Fatal("stale result released a current probe slot")
				}
				cb.RecordSuccess(current)
				if cb.State() != StateHalfOpen {
					t.Fatal("stale success counted toward recovery")
				}
				admitProbe(t, cb)
			})
		}
	}
}

func TestCircuitBreaker_StaleProbeCannotReopenRecoveredCircuit(t *testing.T) {
	cfg := newTestCBConfig()
	cfg.FailureThreshold = 1
	cfg.SuccessThreshold = 1
	cfg.HalfOpenMaxProbes = 2
	cb := NewCircuitBreaker("synthetic", cfg, nil)
	tripProbeCircuit(t, cb)
	first := admitProbe(t, cb)
	late := admitProbe(t, cb)
	cb.RecordSuccess(first)
	cb.RecordFailure(late, serverErr())
	if cb.State() != StateClosed {
		t.Fatal("late probe failure reopened a recovered circuit")
	}
}

func TestCircuitBreaker_DefaultProbeLimit(t *testing.T) {
	for _, configured := range []int{0, -1} {
		t.Run(fmt.Sprint(configured), func(t *testing.T) {
			cfg := newTestCBConfig()
			cfg.HalfOpenMaxProbes = configured
			cb := NewCircuitBreaker("synthetic", cfg, nil)
			tripProbeCircuit(t, cb)
			generation := admitProbe(t, cb)
			if _, allowed := cb.Allow(); allowed {
				t.Fatal("default must admit only one concurrent probe")
			}
			cb.RecordFailure(generation, clientErr())
			admitProbe(t, cb)
		})
	}
}

func TestFailover_ProbeLimitReturnsUnavailable(t *testing.T) {
	for _, attempts := range []int{1, 3} {
		t.Run(fmt.Sprint(attempts), func(t *testing.T) {
			router := makeFailoverRouter(t, "synthetic-model", "synthetic")
			registry := NewCircuitBreakerRegistry(newTestCBConfig(), nil)
			cb := registry.Get("synthetic")
			tripProbeCircuit(t, cb)
			admitProbe(t, cb)
			f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: attempts}, router, nil, registry)
			result, err := f.Execute(context.Background(), "synthetic-model", func(context.Context, string, string) error {
				t.Error("provider called while probe limit was full")
				return nil
			})
			var apiErr *models.APIError
			if result != nil || !errors.As(err, &apiErr) || apiErr.Status != 503 {
				t.Fatalf("rejected request: result=%v err=%v, want explicit 503", result, err)
			}
		})
	}
}

func TestFailover_LateRequestCannotReleaseProbe(t *testing.T) {
	router := makeFailoverRouter(t, "synthetic-model", "synthetic")
	registry := NewCircuitBreakerRegistry(newTestCBConfig(), nil)
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, registry)
	entered := make(chan struct{})
	release := make(chan struct{})
	finished := make(chan error, 1)
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	defer unblock()
	go func() {
		_, err := f.Execute(context.Background(), "synthetic-model", func(context.Context, string, string) error {
			close(entered)
			<-release
			return clientErr()
		})
		finished <- err
	}()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("initial request never reached provider")
	}

	cb := registry.Get("synthetic")
	tripProbeCircuit(t, cb)
	admitProbe(t, cb)
	unblock()
	select {
	case err := <-finished:
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != 400 {
			t.Fatalf("late request error = %v, want 400", err)
		}
	case <-time.After(time.Second):
		t.Fatal("initial request never finished")
	}
	if _, allowed := cb.Allow(); allowed {
		t.Fatal("failover recorded a late request against the new probe generation")
	}
}

func TestFailover_ProbeLimitFallsBack(t *testing.T) {
	router := makeFailoverRouter(t, "synthetic-model", "recovering", "healthy")
	registry := NewCircuitBreakerRegistry(newTestCBConfig(), nil)
	cb := registry.Get("recovering")
	tripProbeCircuit(t, cb)
	admitProbe(t, cb)
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, router, nil, registry)
	for i := 0; i < 4; i++ {
		result, err := f.Execute(context.Background(), "synthetic-model", func(_ context.Context, provider, _ string) error {
			if provider != "healthy" {
				t.Error("saturated recovery provider was called")
			}
			return nil
		})
		if err != nil || result == nil || result.ProviderID != "healthy" {
			t.Fatalf("fallback result=%v error=%v, want healthy provider", result, err)
		}
	}
}
