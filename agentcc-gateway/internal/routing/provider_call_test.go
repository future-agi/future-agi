package routing

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestFailover_CallProviderProbeLifecycle(t *testing.T) {
	for _, outcome := range []struct {
		name string
		err  error
	}{{"success", nil}, {"client-error", clientErr()}, {"server-error", serverErr()}} {
		t.Run(outcome.name, func(t *testing.T) {
			registry := NewCircuitBreakerRegistry(newTestCBConfig(), nil)
			f := NewFailover(config.FailoverConfig{Enabled: true}, nil, nil, registry)
			cb := registry.Get("synthetic")
			tripProbeCircuit(t, cb)
			err := f.CallProvider("synthetic", func() error {
				err := f.CallProvider("synthetic", func() error {
					t.Error("fallback bypassed the occupied probe slot")
					return nil
				})
				var apiErr *models.APIError
				if !errors.As(err, &apiErr) || apiErr.Status != 503 || apiErr.Code != "circuit_open" {
					t.Errorf("saturated fallback error=%v, want circuit_open 503", err)
				}
				if err := f.CallProvider("healthy", func() error { return nil }); err != nil {
					t.Errorf("distinct healthy provider was rejected: %v", err)
				}
				return outcome.err
			})
			if err != outcome.err {
				t.Fatalf("provider error=%v, want %v", err, outcome.err)
			}
			wantState := StateHalfOpen
			if outcome.name == "server-error" {
				wantState = StateOpen
			}
			if cb.State() != wantState {
				t.Fatalf("fallback result state=%v, want %v", cb.State(), wantState)
			}
			expireProbeCooldown(cb)
			if err := f.CallProvider("synthetic", func() error { return nil }); err != nil {
				t.Fatalf("fallback completion leaked its probe slot: %v", err)
			}
			if outcome.name == "success" && cb.State() != StateClosed {
				t.Fatal("successful fallback probes did not close the circuit")
			}
		})
	}
}

func TestFailover_CallProviderStaleResult(t *testing.T) {
	for _, outcome := range []struct {
		name string
		err  error
	}{{"success", nil}, {"client-error", clientErr()}, {"server-error", serverErr()}} {
		t.Run(outcome.name, func(t *testing.T) {
			registry := NewCircuitBreakerRegistry(newTestCBConfig(), nil)
			f := NewFailover(config.FailoverConfig{Enabled: true}, nil, nil, registry)
			cb := registry.Get("synthetic")
			_ = f.CallProvider("synthetic", func() error {
				tripProbeCircuit(t, cb)
				admitProbe(t, cb)
				return outcome.err
			})
			if cb.State() != StateHalfOpen {
				t.Fatal("stale fallback result changed the new recovery state")
			}
			if _, allowed := cb.Allow(); allowed {
				t.Fatal("stale fallback result released a current probe slot")
			}
		})
	}
}

func TestFailover_CallProviderWithoutBreaker(t *testing.T) {
	for _, registry := range []*CircuitBreakerRegistry{nil, NewCircuitBreakerRegistry(config.CircuitBreakerConfig{}, nil)} {
		f := NewFailover(config.FailoverConfig{Enabled: true}, nil, nil, registry)
		called := 0
		for i := 0; i < 3; i++ {
			err := f.CallProvider("synthetic", func() error {
				called++
				return serverErr()
			})
			if err == nil {
				t.Fatal("disabled breaker swallowed the provider error")
			}
		}
		if called != 3 {
			t.Fatalf("disabled breaker allowed %d calls, want 3", called)
		}
	}
}
