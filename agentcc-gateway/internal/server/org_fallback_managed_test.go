package server

// The one way a customer key's request leaves its org's own providers: a
// model fallback chain whose next model only a managed provider serves, in
// an org that turned managed models on (billed to its credits; the hop takes
// the managed credit check, TestManagedFallbackNeedsCredit). Strategy,
// retry, provider failover and circuit breakers stay on the org's providers.
// With managed models off the hop is not taken.

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func managedFallbackOrgConfig(managedOn bool) *tenant.OrgConfig {
	cfg := &tenant.OrgConfig{}
	if managedOn {
		cfg = managedOptIn()
	}
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
	}
	cfg.Routing = &tenant.RoutingConfig{
		ModelFallbacks: map[string][]string{"org-model": {"anthropic/claude-sonnet-5.5"}},
		Failover:       &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{502, 503}},
		Retry:          &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{502, 503}},
	}
	return cfg
}

func TestCustomerKeyModelFallbackReachesAManagedModelOnlyWithManagedOn(t *testing.T) {
	t.Run("managed models on", func(t *testing.T) {
		g := newManagedTestGateway(t, managedFallbackOrgConfig(true))
		g.ledger.SetMicros("org-m", auth.USDToMicros(1))
		g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)

		w := g.chat(t, chatBody("org-model"), nil)
		if w.Code != http.StatusOK {
			t.Fatalf("status %d %s, want 200 from the managed fallback", w.Code, w.Body)
		}
		if n := g.operator.calls(); n != 2 {
			t.Fatalf("org provider calls %d, want 2 (the call and the org's retry)", n)
		}
		if n := g.managed.calls(); n != 1 {
			t.Fatalf("managed calls %d, want 1 (the chain's managed model)", n)
		}
		if bal := g.ledger.BalanceUSD("org-m"); bal >= 1 {
			t.Fatalf("balance %.6f, want the managed fallback billed to the org's credits", bal)
		}
	})
	t.Run("managed models off", func(t *testing.T) {
		g := newManagedTestGateway(t, managedFallbackOrgConfig(false))
		g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)

		w := g.chat(t, chatBody("org-model"), nil)
		if w.Code == http.StatusOK {
			t.Fatalf("status 200 %s, want the org provider's failure", w.Body)
		}
		if n := g.managed.calls(); n != 0 {
			t.Fatalf("managed calls %d with managed models off, want 0", n)
		}
		if n := g.operator.calls(); n != 2 {
			t.Fatalf("org provider calls %d, want 2 (the call and the org's retry)", n)
		}
	})
}
