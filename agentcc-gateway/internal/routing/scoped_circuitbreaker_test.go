package routing

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type orgBreakerFixture struct {
	shared   *CircuitBreakerRegistry
	failover *Failover
}

func newOrgBreakerFixture(t *testing.T, cooldown time.Duration) orgBreakerFixture {
	t.Helper()
	router, err := NewRouter(config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets:         map[string][]config.RoutingTargetConfig{"model": {{Provider: "byok"}}},
	}, map[string]bool{"byok": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(router.Close)
	shared := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, SuccessThreshold: 2, Cooldown: cooldown}, nil)
	return orgBreakerFixture{shared: shared, failover: NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, shared)}
}

// orgAttempt runs one request for org on its own key (the filter rejects the
// operator breaker) and reports whether the provider was called.
func (f orgBreakerFixture) orgAttempt(org string, fail bool) bool {
	called := false
	scoped := f.failover.WithOrgCircuitBreakers(org, func(string) bool { return false })
	_, _ = scoped.Execute(context.Background(), "model", func(context.Context, string, string) error {
		called = true
		if fail {
			return models.ErrUpstreamProvider(http.StatusServiceUnavailable, "synthetic upstream unavailable")
		}
		return nil
	})
	return called
}

func TestOrgBreakerIsolatesOneOrgsFailures(t *testing.T) {
	f := newOrgBreakerFixture(t, time.Hour)
	for i := 0; i < 2; i++ {
		if !f.orgAttempt("org-a", true) {
			t.Fatalf("org-a attempt %d was not admitted before its breaker opened", i)
		}
	}
	if f.orgAttempt("org-a", false) {
		t.Fatal("org-a's breaker did not open after its own failures")
	}
	if !f.orgAttempt("org-b", false) {
		t.Fatal("org-a's failures stopped org-b from routing")
	}
	if got := f.shared.Get("byok").State(); got != StateClosed {
		t.Fatalf("org-a's failures changed the operator breaker: %s", got)
	}
}

func TestOrgBreakerProviderOutageOpensEveryOrg(t *testing.T) {
	f := newOrgBreakerFixture(t, time.Hour)
	for i := 0; i < 2; i++ {
		f.orgAttempt("org-a", true)
		f.orgAttempt("org-b", true)
	}
	for _, org := range []string{"org-a", "org-b"} {
		if f.orgAttempt(org, false) {
			t.Fatalf("%s still burns attempts during a provider-wide outage", org)
		}
	}
}

func TestOrgBreakerHalfOpenRecoveryPerOrg(t *testing.T) {
	f := newOrgBreakerFixture(t, 30*time.Millisecond)
	for i := 0; i < 2; i++ {
		f.orgAttempt("org-a", true)
		f.orgAttempt("org-b", true)
	}
	time.Sleep(40 * time.Millisecond)
	// org-a: two half-open successes close its breaker.
	for i := 0; i < 2; i++ {
		if !f.orgAttempt("org-a", false) {
			t.Fatalf("org-a half-open probe %d refused", i)
		}
	}
	// org-b: a half-open failure re-opens only org-b.
	if !f.orgAttempt("org-b", true) {
		t.Fatal("org-b half-open probe refused")
	}
	if !f.orgAttempt("org-a", false) {
		t.Fatal("org-a did not recover")
	}
	if f.orgAttempt("org-b", false) {
		t.Fatal("org-b's failed probe did not re-open its breaker")
	}
}

func TestOrgBreakerNeedsTheSharedBreakerEnabled(t *testing.T) {
	router, err := NewRouter(config.RoutingConfig{
		DefaultStrategy: "round-robin",
		Targets:         map[string][]config.RoutingTargetConfig{"model": {{Provider: "byok"}}},
	}, map[string]bool{"byok": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer router.Close()
	off := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: false, FailureThreshold: 1}, nil)
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, off)
	calls := 0
	for i := 0; i < 3; i++ {
		_, _ = fo.WithOrgCircuitBreakers("org-a", func(string) bool { return false }).Execute(context.Background(), "model", func(context.Context, string, string) error {
			calls++
			return errors.New("synthetic network failure")
		})
	}
	if calls != 3 {
		t.Fatalf("org breaker applied with circuit breaking disabled: calls=%d", calls)
	}
}

func TestScopedBreakerRegistryIsBounded(t *testing.T) {
	r := NewScopedCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, Cooldown: time.Minute})
	now := time.Unix(1_700_000_000, 0)
	r.now = func() time.Time { return now }
	r.maxEntries = 3

	a := r.Get("org-a", "p")
	if r.Get("org-a", "p") != a {
		t.Fatal("same key returned a different breaker")
	}
	r.Get("org-b", "p")
	now = now.Add(time.Second)
	r.Get("org-a", "p") // refresh org-a
	r.Get("org-c", "p")
	r.Get("org-d", "p") // at cap: evicts the least recently used, org-b
	if r.Len() != 3 {
		t.Fatalf("cap not enforced: %d entries", r.Len())
	}
	if r.Get("org-a", "p") != a {
		t.Fatal("cap evicted a recently used breaker")
	}

	// Idle entries expire: max(10 minutes, 2 × cooldown).
	now = now.Add(11 * time.Minute)
	r.Get("org-e", "p")
	if r.Len() != 1 {
		t.Fatalf("idle breakers were kept: %d entries", r.Len())
	}
}

// Org breakers leave the target in routing, so every attempt can be refused by
// a breaker. Execute must then return an error, not (nil, nil).
func TestFailoverAllAttemptsRefusedReturnsError(t *testing.T) {
	f := newOrgBreakerFixture(t, time.Hour)
	for i := 0; i < 2; i++ {
		f.orgAttempt("org-a", true)
	}
	scoped := f.failover.WithOrgCircuitBreakers("org-a", func(string) bool { return false })
	result, err := scoped.Execute(context.Background(), "model", func(context.Context, string, string) error {
		t.Fatal("open org breaker admitted a call")
		return nil
	})
	if result != nil || err == nil {
		t.Fatalf("all attempts refused: result=%v err=%v, want an error", result, err)
	}
}

// Org breakers count 401 and 403 on top of the configured codes; the shared
// breaker keeps exactly the configured codes, and the config is not mutated.
func TestOrgBreakerCountsRejectedKeysByDefault(t *testing.T) {
	for _, codes := range [][]int{nil, {500}} {
		cfg := config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour, OnStatusCodes: codes}
		shared := NewCircuitBreakerRegistry(cfg, nil)
		orgs := NewScopedCircuitBreakerRegistry(cfg)
		for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
			err := &models.APIError{Status: status, Type: "upstream_error", Message: "synthetic rejected key"}
			org := orgs.Get(fmt.Sprintf("org-%d", status), "byok")
			org.RecordFailure(err)
			if org.State() != StateOpen {
				t.Fatalf("codes=%v: org breaker ignored %d", codes, status)
			}
			op := shared.Get(fmt.Sprintf("operator-%d", status))
			op.RecordFailure(err)
			if op.State() != StateClosed {
				t.Fatalf("codes=%v: shared breaker counted %d", codes, status)
			}
		}
		org := orgs.Get("org-404", "byok")
		org.RecordFailure(&models.APIError{Status: http.StatusNotFound, Type: "upstream_error", Message: "synthetic not found"})
		if org.State() != StateClosed {
			t.Fatalf("codes=%v: org breaker counted 404", codes)
		}
		if len(cfg.OnStatusCodes) != len(codes) {
			t.Fatalf("config codes changed: %v", cfg.OnStatusCodes)
		}
	}
}
