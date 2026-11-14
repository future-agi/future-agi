package routing

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestFailoverCircuitBreakerFilter(t *testing.T) {
	for _, outcome := range []string{"open-admission", "success-recording", "failure-recording"} {
		t.Run(outcome, func(t *testing.T) {
			router, err := NewRouter(config.RoutingConfig{
				DefaultStrategy: "round-robin",
				Targets:         map[string][]config.RoutingTargetConfig{"model": {{Provider: "shared"}}},
			}, map[string]bool{"shared": true}, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer router.Close()
			registry := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: time.Hour}, nil)
			breaker := registry.Get("shared")
			failure := errors.New("synthetic operator failure")
			breaker.RecordFailure(failure)
			if outcome == "open-admission" {
				breaker.RecordFailure(failure)
			}
			global := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, registry)
			filterCalls, providerCalls := 0, 0
			org := global.WithCircuitBreakerFilter(func(id string) bool {
				filterCalls++
				if id != "shared" {
					t.Fatalf("unexpected attempt %q", id)
				}
				return false
			})
			_, _ = org.Execute(context.Background(), "model", func(context.Context, string, string) error {
				providerCalls++
				if outcome == "failure-recording" {
					return errors.New("synthetic org failure")
				}
				return nil
			})
			if filterCalls != 1 || providerCalls != 1 {
				t.Fatalf("org admission used operator breaker: filter=%d calls=%d", filterCalls, providerCalls)
			}
			if outcome != "open-admission" {
				if breaker.State() != StateClosed {
					t.Fatal("org failure opened operator breaker")
				}
				breaker.RecordFailure(failure)
			}
			if breaker.State() != StateOpen {
				t.Fatal("org success reset operator failures")
			}
			_, _ = global.Execute(context.Background(), "model", func(context.Context, string, string) error {
				providerCalls++
				return nil
			})
			if providerCalls != 1 {
				t.Fatal("request-local filter disabled operator admission on shared failover")
			}
		})
	}
}
