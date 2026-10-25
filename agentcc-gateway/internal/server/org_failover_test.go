package server

// A customer key (Django-minted, synced as byok) gets the org's retry,
// failover, circuit breakers and routing strategy over the org's own
// provider instances, and never an operator provider or credential.

import (
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const orgRetryFailoverRouting = `"routing":{"strategy":"round_robin","failover":{"enabled":true,"max_attempts":3,"on_status_codes":[502,503]},"retry":{"enabled":true,"max_retries":2,"initial_delay":"1ms","max_delay":"2ms","multiplier":1,"on_status_codes":[502,503]}}`

// The org's retry runs on its own provider: 503 on the first call, 200 on
// the retry, both with the org's key.
func TestOrgFailoverRetriesTheOrgProvider(t *testing.T) {
	for _, stream := range []bool{false, true} {
		up := startRoutingUpstream(t, func(_ string, n int) bool { return n == 1 })
		srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {up, []string{"org-model"}}, "beta": {up, []string{"org-model"}}}, nil)
		pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model"],"enabled":true}},`+orgRetryFailoverRouting+`}`)

		code, body, _ := routingChat(t, srv, custKeyA, "org-model", stream)
		if code != http.StatusOK {
			t.Fatalf("stream=%v: customer key, upstream 503 on its first call only -> %d %s (upstream calls %v), want 200 after a retry", stream, code, body, up.snapshot())
		}
		if n := up.count(); n != 2 {
			t.Fatalf("stream=%v: upstream calls = %d %v, want 2 (the failure, then the retry)", stream, n, up.snapshot())
		}
		assertOrgCredentialOnly(t, up)
	}
}

// Failover moves a customer key from one org provider to the org's other
// provider, never to a config.yaml provider the org does not have.
func TestOrgFailoverAcrossOrgProvidersOnly(t *testing.T) {
	alpha := startRoutingUpstream(t, func(string, int) bool { return true })
	beta := startRoutingUpstream(t, nil)
	gamma := startRoutingUpstream(t, nil) // the operator's only: the org has no gamma
	srv := orgRoutingGateway(t, map[string]routingOp{
		"alpha": {alpha, []string{"fo-model"}},
		"beta":  {beta, []string{"fo-model"}},
		"gamma": {gamma, []string{"fo-model"}},
	}, func(cfg *config.Config) {
		// The operator's own failover and breakers are on: a customer key
		// still never reaches gamma or the operator's credentials.
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{502, 503}}
	})
	pushOrgConfig(t, srv, orgA, `{"providers":{`+
		`"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["fo-model"],"enabled":true},`+
		`"beta":{"api_key":"org-beta-key","api_format":"openai","models":["fo-model"],"enabled":true}},`+
		`"routing":{"failover":{"enabled":true,"max_attempts":3,"on_status_codes":[502,503]}}}`)

	for i := 0; i < 4; i++ {
		code, body, _ := routingChat(t, srv, custKeyA, "fo-model", i%2 == 1)
		if code != http.StatusOK {
			t.Fatalf("request %d: customer key with alpha always 503 -> %d %s, want 200 from beta", i, code, body)
		}
	}
	if beta.count() == 0 {
		t.Fatalf("beta was never called; alpha calls %v", alpha.snapshot())
	}
	if n := gamma.count(); n != 0 {
		t.Fatalf("the operator-only provider gamma was called %d times by a customer key: %v", n, gamma.snapshot())
	}
	assertOrgCredentialOnly(t, alpha, beta)
}

// With one org provider and every attempt failing, a customer key's
// failover and retries end on the org's provider: no operator provider that
// serves the same model is ever tried, and the answer is the upstream error.
func TestOrgFailoverNeverReachesAnOperatorProvider(t *testing.T) {
	alpha := startRoutingUpstream(t, func(string, int) bool { return true })
	beta := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {alpha, []string{"org-model"}}, "beta": {beta, []string{"org-model"}}}, func(cfg *config.Config) {
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{502, 503}}
	})
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model"],"enabled":true}},`+
		`"routing":{"fallback_enabled":true,"model_fallbacks":{"org-model":["other-model"]},`+
		`"failover":{"enabled":true,"max_attempts":3,"on_status_codes":[502,503]},`+
		`"retry":{"enabled":true,"max_retries":1,"initial_delay":"1ms","max_delay":"2ms","multiplier":1,"on_status_codes":[502,503]}}}`)

	for _, stream := range []bool{false, true} {
		code, body, _ := routingChat(t, srv, custKeyA, "org-model", stream)
		if code == http.StatusOK {
			t.Fatalf("stream=%v: -> 200, want the org provider's failure", stream)
		}
		_ = body
	}
	if n := beta.count(); n != 0 {
		t.Fatalf("operator provider beta was called %d times by a customer key: %v", n, beta.snapshot())
	}
	if n := alpha.count(); n != 4 {
		t.Fatalf("alpha calls = %d %v, want 4 (1 + 1 retry, per request)", n, alpha.snapshot())
	}
	assertOrgCredentialOnly(t, alpha)
}

const orgBreakerRouting = `"routing":{"failover":{"enabled":true,"max_attempts":2,"on_status_codes":[502,503]},"circuit_breaker":{"enabled":true,"failure_threshold":1,"success_threshold":1,"cooldown":"60s","on_status_codes":[502,503]}}`

// The org's circuit breaker opens on its own provider: after one failure
// the next requests are refused without an upstream call.
func TestOrgBreakerOpensOnTheOrgProvider(t *testing.T) {
	up := startRoutingUpstream(t, func(string, int) bool { return true })
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {up, []string{"org-model"}}, "beta": {up, []string{"org-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model"],"enabled":true}},`+orgBreakerRouting+`}`)

	var perReq []int
	prev := 0
	var last string
	for i := 0; i < 4; i++ {
		_, body, _ := routingChat(t, srv, custKeyA, "org-model", false)
		n := up.count()
		perReq = append(perReq, n-prev)
		prev, last = n, body
	}
	want := []int{1, 0, 0, 0}
	for i := range want {
		if perReq[i] != want[i] {
			t.Fatalf("upstream calls per request = %v, want %v (the circuit opens after one failure); last body %s", perReq, want, last)
		}
	}
	if !strings.Contains(last, "open circuit") {
		t.Fatalf("a request while the circuit is open -> %s, want the open-circuit refusal", last)
	}
	assertOrgCredentialOnly(t, up)
}

// Breakers are per org and never the operator's: org A's open circuit does
// not refuse org B's provider of the same id, nor the operator's own
// failover for an internal key, and the operator's open circuit does not
// refuse an org's customer key.
func TestOrgBreakerIsNotSharedWithAnotherOrgOrTheOperator(t *testing.T) {
	var failing atomic.Bool
	failing.Store(true)
	alpha := startRoutingUpstream(t, func(string, int) bool { return failing.Load() })
	beta := startRoutingUpstream(t, func(string, int) bool { return failing.Load() })
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {alpha, []string{"org-model"}}, "beta": {beta, []string{"org-model"}}}, func(cfg *config.Config) {
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{502, 503}}
		cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, SuccessThreshold: 1, Cooldown: time.Minute, OnStatusCodes: []int{502, 503}}
	})
	orgCfg := func(key string) string {
		return `{"providers":{"alpha":{"api_key":"` + key + `","api_format":"openai","models":["org-model"],"enabled":true}},` + orgBreakerRouting + `}`
	}
	pushOrgConfig(t, srv, orgA, orgCfg("org-a-alpha-key"))
	pushOrgConfig(t, srv, orgB, orgCfg("org-b-alpha-key"))

	// Org A opens its circuit on alpha.
	for i := 0; i < 2; i++ {
		routingChat(t, srv, custKeyA, "org-model", false)
	}
	// The operator's own service key (no org) opens the operator's circuits
	// on alpha and beta.
	for i := 0; i < 2; i++ {
		routingChat(t, srv, operatorKey, "org-model", false)
	}
	if code, body, _ := routingChat(t, srv, operatorKey, "org-model", false); !strings.Contains(body, "open circuit") {
		t.Fatalf("the operator's circuits did not open: %d %s", code, body)
	}

	failing.Store(false)
	before := alpha.snapshot()
	code, body, _ := routingChat(t, srv, custKeyB, "org-model", false)
	if code != http.StatusOK {
		t.Fatalf("org B after org A's and the operator's circuits opened -> %d %s, want 200 from its own alpha", code, body)
	}
	after := alpha.snapshot()
	if len(after) != len(before)+1 || after[len(after)-1].Auth != "Bearer org-b-alpha-key" {
		t.Fatalf("org B's request: alpha calls %v -> %v, want one more call with org B's key", before, after)
	}
	// Org A's circuit is still open: no call.
	n := alpha.count()
	routingChat(t, srv, custKeyA, "org-model", false)
	if alpha.count() != n {
		t.Fatalf("org A's open circuit let a call through")
	}
}

// round_robin across the org's two providers alternates.
func TestOrgStrategyRoundRobinAcrossOrgProviders(t *testing.T) {
	up1 := startRoutingUpstream(t, nil)
	up2 := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {up1, []string{"rr-model"}}, "beta": {up2, []string{"rr-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["rr-model"],"enabled":true},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["rr-model"],"enabled":true}},"routing":{"strategy":"round_robin","failover":{"enabled":true}}}`)
	var seq []string
	for i := 0; i < 12; i++ {
		b1, b2 := up1.count(), up2.count()
		code, body, hdr := routingChat(t, srv, custKeyA, "rr-model", i == 11)
		if code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
		switch {
		case up1.count() > b1:
			seq = append(seq, "alpha")
		case up2.count() > b2:
			seq = append(seq, "beta")
		}
		if got := hdr.Get("x-agentcc-provider"); got != "" && got != "alpha" && got != "beta" {
			t.Fatalf("x-agentcc-provider = %q, want the bare org provider id", got)
		}
	}
	for i := 1; i < len(seq); i++ {
		if seq[i] == seq[i-1] {
			t.Fatalf("round_robin over two org providers, 12 requests -> %v, want alternation", seq)
		}
	}
	assertOrgCredentialOnly(t, up1, up2)
}

// weighted uses each org provider's weight (1 : 3).
func TestOrgStrategyWeightedUsesOrgWeights(t *testing.T) {
	up1 := startRoutingUpstream(t, nil)
	up2 := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {up1, []string{"w-model"}}, "beta": {up2, []string{"w-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["w-model"],"enabled":true,"weight":0.25},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["w-model"],"enabled":true,"weight":0.75}},"routing":{"strategy":"weighted"}}`)
	const n = 400
	for i := 0; i < n; i++ {
		if code, body, _ := routingChat(t, srv, custKeyA, "w-model", false); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	a := up1.count()
	if a < 50 || a > 150 { // expect 100 of 400; both bounds are > 5 sigma away
		t.Fatalf("weighted 0.25/0.75 over %d requests: alpha %d, beta %d; want about 100 / 300", n, a, up2.count())
	}
	assertOrgCredentialOnly(t, up1, up2)
}

// least_latency prefers the org provider that answered faster.
func TestOrgStrategyLeastLatencyPrefersTheFasterOrgProvider(t *testing.T) {
	slow := startRoutingUpstream(t, nil)
	slow.delay = 120 * time.Millisecond
	fast := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {slow, []string{"ll-model"}}, "beta": {fast, []string{"ll-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["ll-model"],"enabled":true},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["ll-model"],"enabled":true}},"routing":{"strategy":"least_latency"}}`)
	for i := 0; i < 10; i++ {
		if code, body, _ := routingChat(t, srv, custKeyA, "ll-model", false); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	if s, f := slow.count(), fast.count(); s > 2 || f < 8 {
		t.Fatalf("least_latency, alpha +120 ms: alpha %d, beta %d of 10; want the slow one tried at most twice", s, f)
	}
	assertOrgCredentialOnly(t, slow, fast)
}

// cost_optimized prices each org provider by its own id (the target ids are
// org-scoped; the price lookup sees the provider id).
func TestOrgStrategyCostOptimizedPricesOrgProviders(t *testing.T) {
	up1 := startRoutingUpstream(t, nil)
	up2 := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {up1, []string{"c-model"}}, "beta": {up2, []string{"c-model"}}}, nil)
	var asked []string
	srv.Handlers().SetInjectedDeps(InjectedDeps{Prices: func(providerID, model, _ string) (models.PriceCard, bool) {
		asked = append(asked, providerID)
		switch providerID {
		case "alpha":
			return models.PriceCard{Prompt: 5e-6, Completion: 5e-6}, true
		case "beta":
			return models.PriceCard{Prompt: 1e-6, Completion: 1e-6}, true
		}
		return models.PriceCard{}, false
	}})
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["c-model"],"enabled":true},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["c-model"],"enabled":true}},"routing":{"strategy":"cost_optimized"}}`)
	for i := 0; i < 6; i++ {
		if code, body, _ := routingChat(t, srv, custKeyA, "c-model", false); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	if a, b := up1.count(), up2.count(); a != 0 || b != 6 {
		t.Fatalf("cost_optimized, beta cheaper: alpha %d, beta %d (price lookups for %v); want all 6 on beta", a, b, asked)
	}
	assertOrgCredentialOnly(t, up1, up2)
}

// Without the Provider Failover switch, a customer key is not moved to the
// org's other provider: its retry runs on the provider it was routed to, and
// the model fallback switch alone moves it down its chain, not across
// providers.
func TestOrgFailoverOnlyWithTheProviderFailoverSwitch(t *testing.T) {
	alpha := startRoutingUpstream(t, func(string, int) bool { return true })
	beta := startRoutingUpstream(t, nil)
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {alpha, []string{"sw-model"}}, "beta": {beta, []string{"sw-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{`+
		`"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["sw-model"],"enabled":true},`+
		`"beta":{"api_key":"org-beta-key","api_format":"openai","models":["sw-model"],"enabled":true}},`+
		`"routing":{"fallback_enabled":true,"retry":{"enabled":true,"max_retries":1,"initial_delay":"1ms","max_delay":"2ms","multiplier":1,"on_status_codes":[502,503]}}}`)
	code, body, _ := routingChat(t, srv, custKeyA, "sw-model", false)
	if code == http.StatusOK {
		t.Fatalf("-> 200 %s, want alpha's failure: provider failover is off", body)
	}
	if a, b := alpha.count(), beta.count(); a != 2 || b != 0 {
		t.Fatalf("alpha %d, beta %d; want 2 (1 + 1 retry) / 0", a, b)
	}
}

// adaptive learns from the org's own providers: after its learning requests
// it prefers the faster one. (The fast one answers in 5 ms: the latency
// tracker counts whole milliseconds, and adaptive reads 0 ms as not measured.)
func TestOrgStrategyAdaptiveLearnsFromOrgProviders(t *testing.T) {
	slow := startRoutingUpstream(t, nil)
	slow.delay = 80 * time.Millisecond
	fast := startRoutingUpstream(t, nil)
	fast.delay = 5 * time.Millisecond
	srv := orgRoutingGateway(t, map[string]routingOp{"alpha": {slow, []string{"ad-model"}}, "beta": {fast, []string{"ad-model"}}}, nil)
	pushOrgConfig(t, srv, orgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["ad-model"],"enabled":true},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["ad-model"],"enabled":true}},"routing":{"adaptive":{"enabled":true,"learning_requests":4}}}`)
	const n = 30
	for i := 0; i < n; i++ {
		if code, body, _ := routingChat(t, srv, custKeyA, "ad-model", false); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	if s, f := slow.count(), fast.count(); f < 20 {
		t.Fatalf("adaptive, alpha +80 ms: alpha %d, beta %d of %d; want the fast one preferred after learning", s, f, n)
	}
	assertOrgCredentialOnly(t, slow, fast)
}
