package routing

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func budgetRouter(t *testing.T) *Router {
	t.Helper()
	router, err := NewRouter(config.RoutingConfig{
		DefaultStrategy: "least-latency",
		Targets: map[string][]config.RoutingTargetConfig{"model": {
			{Provider: "a"}, {Provider: "b"}, {Provider: "c"}, {Provider: "d"},
		}},
	}, map[string]bool{"a": true, "b": true, "c": true, "d": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(router.Close)
	// Deterministic order, independent of the round-robin counter.
	for i, id := range []string{"a", "b", "c", "d"} {
		router.RecordLatency(id, time.Duration(i+1)*time.Millisecond)
	}
	return router
}

func TestFailoverRefusalsDoNotSpendAttempts(t *testing.T) {
	cases := []struct {
		name        string
		budget      int
		refused     []string
		failed      []string
		calls       []string
		provider    string
		retry       bool
		unavailable bool
	}{
		{name: "healthy-third-budget-one", budget: 1, refused: []string{"a", "b"}, calls: []string{"c"}, provider: "c"},
		{name: "healthy-third-budget-two", budget: 2, refused: []string{"a", "b"}, calls: []string{"c"}, provider: "c"},
		{name: "healthy-third-retry", budget: 1, refused: []string{"a", "b"}, calls: []string{"c"}, provider: "c", retry: true},
		{name: "all-refused", budget: 2, refused: []string{"a", "b", "c", "d"}, unavailable: true},
		{name: "all-refused-retry", budget: 1, refused: []string{"a", "b", "c", "d"}, retry: true, unavailable: true},
		{name: "upstream-budget", budget: 2, refused: []string{"a"}, failed: []string{"b", "c"}, calls: []string{"b", "c"}},
		{name: "mixed-budget", budget: 2, refused: []string{"a", "c"}, failed: []string{"b"}, calls: []string{"b", "d"}, provider: "d"},
		{name: "last-real-error", budget: 2, refused: []string{"a", "c", "d"}, failed: []string{"b"}, calls: []string{"b"}},
	}
	for _, scope := range []string{"operator", "org"} {
		for _, tc := range cases {
			t.Run(scope+"/"+tc.name, func(t *testing.T) {
				router := budgetRouter(t)
				registry := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
				var retryer *Retryer
				if tc.retry {
					retryer = NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: time.Nanosecond})
				}
				fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: tc.budget}, router, retryer, registry)
				if scope == "org" {
					fo = fo.WithOrgCircuitBreakers("org-a", func(string) bool { return false })
				}
				refusedBreakers := map[string]*CircuitBreaker{}
				refusedAt := map[string]time.Time{}
				for _, id := range tc.refused {
					cb := fo.circuitBreakerFor(id)
					cb.RecordFailure(errors.New("synthetic failure"))
					refusedBreakers[id], refusedAt[id] = cb, cb.lastFailure
				}
				var calls, considered []string
				fo = fo.WithCircuitBreakerFilter(func(id string) bool {
					considered = append(considered, id)
					return scope == "operator"
				})
				upstreamErr := models.ErrUpstreamProvider(503, "synthetic upstream failure")
				result, err := fo.Execute(context.Background(), "model", func(_ context.Context, id, _ string) error {
					calls = append(calls, id)
					for _, failed := range tc.failed {
						if id == failed {
							return upstreamErr
						}
					}
					return nil
				})
				if !reflect.DeepEqual(calls, tc.calls) {
					t.Fatalf("breaker refusals consumed or retried the upstream budget: calls=%v want=%v result=%+v err=%v", calls, tc.calls, result, err)
				}
				for id, cb := range refusedBreakers {
					if cb.State() != StateOpen || cb.lastFailure != refusedAt[id] || cb.failures != 1 {
						t.Fatalf("refused target %s recorded an outcome", id)
					}
				}
				seen := map[string]bool{}
				for _, id := range considered {
					if seen[id] {
						t.Fatalf("target reconsidered: %v", considered)
					}
					seen[id] = true
				}
				switch {
				case tc.provider != "":
					if err != nil || result == nil || result.ProviderID != tc.provider {
						t.Fatalf("healthy target not returned: result=%+v err=%v", result, err)
					}
					if result.Attempts != len(tc.calls) || result.OriginalProvider != "a" || !result.FallbackUsed {
						t.Fatalf("incorrect admitted-attempt metadata: %+v", result)
					}
				case tc.unavailable:
					var apiErr *models.APIError
					if result != nil || !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable || apiErr.Code != "all_providers_unavailable" || !strings.Contains(apiErr.Message, "unavailable") {
						t.Fatalf("all refused: result=%+v err=%v; want typed 503 unavailable", result, err)
					}
					if !reflect.DeepEqual(considered, []string{"a", "b", "c", "d"}) {
						t.Fatalf("each target must be considered once: %v", considered)
					}
				default:
					if result != nil || err != upstreamErr {
						t.Fatalf("last real provider error lost: result=%+v err=%v", result, err)
					}
				}
			})
		}
	}
}

func TestFailoverSelectionExhaustedReturnsUnavailable(t *testing.T) {
	router := budgetRouter(t)
	for _, id := range []string{"a", "b", "c", "d"} {
		router.SetHealthy(id, false)
	}
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, router, nil, nil)
	result, err := fo.Execute(context.Background(), "model", func(context.Context, string, string) error {
		t.Fatal("unavailable target was called")
		return nil
	})
	var apiErr *models.APIError
	if result != nil || !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable || apiErr.Code != "all_providers_unavailable" {
		t.Fatalf("selection exhausted: result=%+v err=%v; want typed 503 unavailable", result, err)
	}
}

func TestFailoverCancellationDuringRefusalTraversal(t *testing.T) {
	for _, realErrorFirst := range []bool{false, true} {
		t.Run(fmt.Sprint(realErrorFirst), func(t *testing.T) {
			router := budgetRouter(t)
			registry := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
			fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, registry)
			refused := "a"
			if realErrorFirst {
				refused = "b"
			}
			registry.Get(refused).RecordFailure(errors.New("synthetic failure"))
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var considered, calls []string
			fo = fo.WithCircuitBreakerFilter(func(id string) bool {
				considered = append(considered, id)
				if id == refused {
					cancel()
				}
				return true
			})
			upstreamErr := models.ErrUpstreamProvider(503, "synthetic upstream failure")
			result, err := fo.Execute(ctx, "model", func(_ context.Context, id, _ string) error {
				calls = append(calls, id)
				return upstreamErr
			})
			wantErr := error(context.Canceled)
			wantConsidered := []string{"a"}
			var wantCalls []string
			if realErrorFirst {
				wantErr = upstreamErr
				wantConsidered = []string{"a", "b"}
				wantCalls = []string{"a"}
			}
			if result != nil || err != wantErr || !reflect.DeepEqual(calls, wantCalls) || !reflect.DeepEqual(considered, wantConsidered) {
				t.Fatalf("cancellation did not stop traversal: result=%+v err=%v calls=%v considered=%v; want err=%v calls=%v considered=%v", result, err, calls, considered, wantErr, wantCalls, wantConsidered)
			}
		})
	}
}
