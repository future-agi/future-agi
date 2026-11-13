package routing

import (
	"context"
	"errors"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestFailoverRefusalsDoNotSpendAttempts(t *testing.T) {
	for _, scope := range []string{"operator", "org"} {
		for _, scenario := range []string{"healthy-third", "all-refused", "upstream-budget"} {
			t.Run(scope+"/"+scenario, func(t *testing.T) {
				targets := []config.RoutingTargetConfig{{Provider: "a"}, {Provider: "b"}, {Provider: "c"}}
				if scenario == "upstream-budget" {
					targets = append(targets, config.RoutingTargetConfig{Provider: "d"})
				}
				router, err := NewRouter(config.RoutingConfig{
					DefaultStrategy: "least-latency",
					Targets:         map[string][]config.RoutingTargetConfig{"model": targets},
				}, map[string]bool{"a": true, "b": true, "c": true, "d": true}, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer router.Close()
				// Deterministic order, independent of the round-robin counter.
				for i, id := range []string{"a", "b", "c", "d"} {
					router.RecordLatency(id, time.Duration(i+1)*time.Millisecond)
				}
				registry := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
				fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, router, nil, registry)
				if scope == "org" {
					fo = fo.WithOrgCircuitBreakers("org-a", func(string) bool { return false })
				}
				refused := []string{"a", "b"}
				if scenario == "all-refused" {
					refused = []string{"a", "b", "c"}
				} else if scenario == "upstream-budget" {
					refused = []string{"a"}
				}
				for _, id := range refused {
					fo.circuitBreakerFor(id).RecordFailure(errors.New("synthetic failure"))
				}
				var calls, considered []string
				fo = fo.WithCircuitBreakerFilter(func(id string) bool {
					considered = append(considered, id)
					return scope == "operator"
				})
				upstreamErr := models.ErrUpstreamProvider(503, "synthetic upstream failure")
				result, err := fo.Execute(context.Background(), "model", func(_ context.Context, id, _ string) error {
					calls = append(calls, id)
					if scenario == "upstream-budget" {
						return upstreamErr
					}
					return nil
				})
				switch scenario {
				case "healthy-third":
					if err != nil || result == nil || result.ProviderID != "c" || !reflect.DeepEqual(calls, []string{"c"}) {
						t.Fatalf("breaker refusals consumed the upstream budget: result=%+v err=%v calls=%v", result, err, calls)
					}
					if result.Attempts != 1 || result.OriginalProvider != "a" || !result.FallbackUsed {
						t.Fatalf("incorrect admitted-attempt metadata: %+v", result)
					}
				case "all-refused":
					var apiErr *models.APIError
					if result != nil || !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable || !strings.Contains(apiErr.Message, "unavailable") || len(calls) != 0 {
						t.Fatalf("all refused: result=%+v err=%v calls=%v; want a 503 unavailable error without upstream calls", result, err, calls)
					}
					if !reflect.DeepEqual(considered, []string{"a", "b", "c"}) {
						t.Fatalf("each target must be considered once: %v", considered)
					}
				case "upstream-budget":
					if result != nil || err != upstreamErr || !reflect.DeepEqual(calls, []string{"b", "c"}) {
						t.Fatalf("admitted calls must consume MaxAttempts: result=%+v err=%v calls=%v", result, err, calls)
					}
				}
			})
		}
	}
}
