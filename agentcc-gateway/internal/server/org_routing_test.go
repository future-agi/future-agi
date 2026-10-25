package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's strategy, complexity and circuit breaker
// change what the gateway does, and every org routing key has a runtime owner.

const orgRouteOrg = "org-route"

// orgRouteUpstream is one provider's OpenAI-shaped upstream. It counts the chat
// completions it served, records the models it was asked for, and fails
// every call while failing is set.
type orgRouteUpstream struct {
	*httptest.Server
	calls   atomic.Int32
	failing atomic.Bool
	mu      sync.Mutex
	models  []string
}

func startOrgRouteUpstream(t testing.TB) *orgRouteUpstream {
	t.Helper()
	u := &orgRouteUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		var body struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		u.calls.Add(1)
		u.mu.Lock()
		u.models = append(u.models, body.Model)
		u.mu.Unlock()
		if u.failing.Load() {
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "chatcmpl-route", "object": "chat.completion", "created": 1, "model": body.Model,
			"choices": []any{map[string]any{"index": 0, "message": map[string]string{"role": "assistant", "content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]int{"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
		})
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *orgRouteUpstream) lastModel() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.models) == 0 {
		return ""
	}
	return u.models[len(u.models)-1]
}

// orgRouteGateway is a real server with an internal key for orgRouteOrg, so requests
// fail over across config.yaml providers (a customer key never does).
type orgRouteGateway struct {
	srv     *Server
	tenants *tenant.Store
	ups     map[string]*orgRouteUpstream
	mu      sync.Mutex
	meta    map[string]string // the last request's metadata, at setAgentccHeaders
}

func newOrgRouteGateway(t testing.TB, mutate func(cfg *config.Config, ups map[string]*orgRouteUpstream)) *orgRouteGateway {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "route", Key: "sk-route", Owner: "t", KeyType: "internal",
		Metadata: map[string]string{"org_id": orgRouteOrg},
	}}
	ups := map[string]*orgRouteUpstream{}
	for _, id := range []string{"cheap", "mid", "pricey"} {
		ups[id] = startOrgRouteUpstream(t)
		cfg.Providers[id] = config.ProviderConfig{BaseURL: ups[id].URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"m"}}
	}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {{Provider: "pricey"}, {Provider: "cheap"}, {Provider: "mid"}}}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{http.StatusBadGateway}}
	if mutate != nil {
		mutate(cfg, ups)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	g := &orgRouteGateway{srv: srv, tenants: tenants, ups: ups}
	srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		if hook != hookSetAgentccHeaders {
			return
		}
		meta := make(map[string]string, len(rc.Metadata))
		for k, v := range rc.Metadata {
			meta[k] = v
		}
		g.mu.Lock()
		g.meta = meta
		g.mu.Unlock()
	}
	return g
}

func (g *orgRouteGateway) setOrg(t testing.TB, routingJSON string) *tenant.OrgConfig {
	t.Helper()
	var r tenant.RoutingConfig
	if err := json.Unmarshal([]byte(routingJSON), &r); err != nil {
		t.Fatalf("org routing %s: %v", routingJSON, err)
	}
	cfg := &tenant.OrgConfig{Routing: &r, Meta: &tenant.ConfigMeta{Version: 3, ConfigID: "cfg-" + t.Name()}}
	g.tenants.Set(orgRouteOrg, cfg)
	return cfg
}

// chat sends a non-stream chat for model and returns the response, which
// must be a 200.
func (g *orgRouteGateway) chat(t *testing.T, model string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	w := g.send(model, headers...)
	if w.Code != http.StatusOK {
		t.Fatalf("chat %s: status %d, body %s", model, w.Code, w.Body.String())
	}
	return w
}

// send sends a non-stream chat for model and returns the response.
func (g *orgRouteGateway) send(model string, headers ...string) *httptest.ResponseRecorder {
	body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hi"}]}`, model)
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-route")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func (g *orgRouteGateway) lastMeta(key string) string {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.meta[key]
}

// orgRoutePrices prices m at cheap 1, mid 2, pricey 3 (USD per million tokens).
type orgRoutePrices struct {
	mu    sync.Mutex
	cards map[string]float64
}

func (p *orgRoutePrices) lookup(providerID, model, _ string) (models.PriceCard, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	usd, ok := p.cards[providerID+"|"+model]
	return models.PriceCard{Prompt: usd / 1e6, Completion: usd / 1e6, Source: models.PriceSourceCatalog}, ok
}

func newOrgRoutePrices() *orgRoutePrices {
	return &orgRoutePrices{cards: map[string]float64{"cheap|m": 1, "mid|m": 2, "pricey|m": 3}}
}

func TestOrgStrategyApplied(t *testing.T) {
	g := newOrgRouteGateway(t, nil)
	prices := newOrgRoutePrices()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{Prices: prices.lookup})

	g.setOrg(t, `{"strategy":"cost_optimized"}`)
	for i := 0; i < 12; i++ {
		w := g.chat(t, "m")
		if got := w.Header().Get("x-agentcc-provider"); got != "cheap" {
			t.Fatalf("request %d under the org's cost_optimized strategy went to %q, want cheap", i, got)
		}
	}
	if n := g.ups["pricey"].calls.Load() + g.ups["mid"].calls.Load(); n != 0 {
		t.Fatalf("pricier upstreams served %d calls, want 0", n)
	}
	for key, want := range map[string]string{
		"org_routing_strategy":     "cost_optimized",
		"routing_strategy_applied": "cost-optimized",
		"route_reason.strategy":    "cost-optimized",
		"routing_strategy":         "cost-optimized",
	} {
		if got := g.lastMeta(key); got != want {
			t.Errorf("metadata %s = %q, want %q", key, got, want)
		}
	}

	t.Run("prices are read when the strategy runs", func(t *testing.T) {
		prices.mu.Lock()
		prices.cards["mid|m"] = 0.5
		prices.mu.Unlock()
		if got := g.chat(t, "m").Header().Get("x-agentcc-provider"); got != "mid" {
			t.Fatalf("after mid's price fell below cheap's: routed to %q, want mid", got)
		}
	})

	t.Run("deps installed after the org router was built are the ones used", func(t *testing.T) {
		late := &orgRoutePrices{cards: map[string]float64{"cheap|m": 9, "mid|m": 9, "pricey|m": 0.1}}
		g.srv.handlers.SetInjectedDeps(InjectedDeps{Prices: late.lookup})
		if got := g.chat(t, "m").Header().Get("x-agentcc-provider"); got != "pricey" {
			t.Fatalf("after new prices were installed: routed to %q, want pricey", got)
		}
	})

	t.Run("an org without a strategy keeps the global round-robin", func(t *testing.T) {
		g.setOrg(t, `{"retry":{"enabled":false}}`)
		before := map[string]int32{}
		for id, u := range g.ups {
			before[id] = u.calls.Load()
		}
		for i := 0; i < 9; i++ {
			g.chat(t, "m")
		}
		for id, u := range g.ups {
			if u.calls.Load() == before[id] {
				t.Fatalf("round-robin never reached %s", id)
			}
		}
		if got := g.lastMeta("routing_strategy_applied"); got != "" {
			t.Fatalf("routing_strategy_applied = %q for an org with no strategy, want unset", got)
		}
	})

	t.Run("only a request the org's router selects for is stamped", func(t *testing.T) {
		h := g.srv.handlers
		orgCfg := g.setOrg(t, `{"strategy":"cost_optimized"}`)
		stamp := func(keyType, model string) string {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = model
			rc.Metadata["key_type"] = keyType
			h.applyOrgRoutingOverrides(orgCfg, rc)
			if rc.Metadata["routing_strategy_applied"] != rc.Metadata["route_reason.strategy"] {
				t.Fatalf("routing_strategy_applied %q and route_reason.strategy %q disagree",
					rc.Metadata["routing_strategy_applied"], rc.Metadata["route_reason.strategy"])
			}
			return rc.Metadata["routing_strategy_applied"]
		}
		if got := stamp("internal", "m"); got != "cost-optimized" {
			t.Fatalf("internal key, routed model: stamp %q, want cost-optimized", got)
		}
		if got := stamp("byok", "m"); got != "" {
			t.Fatalf("customer key (never fails over across gateway providers): stamp %q, want none", got)
		}
		if got := stamp("internal", "unrouted"); got != "" {
			t.Fatalf("model with no gateway targets: stamp %q, want none", got)
		}
	})

	t.Run("adaptive builds the real adaptive strategy", func(t *testing.T) {
		o := g.setOrg(t, `{"adaptive":{"enabled":true,"learning_requests":5}}`)
		g.chat(t, "m")
		if got := g.lastMeta("routing_strategy_applied"); got != "adaptive" {
			t.Fatalf("routing_strategy_applied = %q, want adaptive", got)
		}
		if got := g.srv.handlers.effectiveFailover(o); got == g.srv.handlers.failover.Load() {
			t.Fatal("an adaptive org runs on the global failover, want its own router")
		}
	})
}

func TestOrgComplexityTier(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, ups map[string]*orgRouteUpstream) {
		p := cfg.Providers["cheap"]
		p.Models = []string{"m", "m-small", "m-large"}
		cfg.Providers["cheap"] = p
	})
	g.setOrg(t, `{"complexity":{"enabled":true,"default_tier":"simple","tiers":{
		"simple":{"max_score":30,"model":"m-small"},
		"complex":{"max_score":100,"model":"m-large"}}}}`)

	g.chat(t, "anything")
	if got := g.ups["cheap"].lastModel(); got != "m-small" {
		t.Fatalf("a one-line prompt reached the upstream as %q, want the simple tier's m-small", got)
	}
	for key, want := range map[string]string{
		"complexity_tier":              "simple",
		"route_reason.complexity_tier": "simple",
		"complexity_original_model":    "anything",
		"complexity_source":            "org",
	} {
		if got := g.lastMeta(key); got != want {
			t.Errorf("metadata %s = %q, want %q", key, got, want)
		}
	}

	g.chat(t, "anything", "x-agentcc-complexity-override", "complex")
	if got := g.ups["cheap"].lastModel(); got != "m-large" {
		t.Fatalf("an explicit complex tier reached the upstream as %q, want m-large", got)
	}
	if got := g.lastMeta("complexity_tier"); got != "complex" {
		t.Fatalf("complexity_tier = %q, want complex", got)
	}

	t.Run("a provider-qualified model is never remapped", func(t *testing.T) {
		g.chat(t, "cheap/m")
		if got := g.ups["cheap"].lastModel(); got != "cheap/m" {
			t.Fatalf("cheap/m reached the upstream as %q, want it unmapped (cheap/m)", got)
		}
	})

	t.Run("an org without complexity keeps the model", func(t *testing.T) {
		g.setOrg(t, `{"strategy":"round_robin"}`)
		g.chat(t, "m-large")
		if got := g.ups["cheap"].lastModel(); got != "m-large" {
			t.Fatalf("model reached the upstream as %q, want m-large", got)
		}
	})
}

func TestOrgCircuitBreakerHonoured(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true} // global threshold: the default 5
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {{Provider: "pricey"}, {Provider: "cheap", Priority: 1}}}
	})
	h := g.srv.handlers
	orgCfg := g.setOrg(t, `{"strategy":"cost_optimized","failover":{"enabled":true,"max_attempts":2,"on_status_codes":[502]},
		"circuit_breaker":{"enabled":true,"failure_threshold":2,"cooldown":"1m"}}`)

	fo := h.effectiveFailover(orgCfg)
	if fo == nil || fo == h.failover.Load() {
		t.Fatal("effectiveFailover returned the global failover for an org with its own circuit breaker")
	}
	orgReg := fo.Breakers()
	globalReg := h.failover.Load().Breakers()
	if orgReg == nil || orgReg == globalReg {
		t.Fatalf("org failover breakers = %p (global %p), want the org's own registry", orgReg, globalReg)
	}

	key := routing.BreakerKey("pricey", "m", "")
	orgReg.Get(key).RecordFailure(&models.APIError{Status: http.StatusBadGateway})
	if orgReg.Get(key).State() != routing.StateClosed {
		t.Fatal("the org circuit opened after 1 failure at threshold 2")
	}
	orgReg.Get(key).RecordFailure(&models.APIError{Status: http.StatusBadGateway})
	if orgReg.Get(key).State() != routing.StateOpen {
		t.Fatalf("the org circuit is %v after 2 failures, want open (org threshold 2, global 5)", orgReg.Get(key).State())
	}
	if globalReg.Get(key).State() != routing.StateClosed {
		t.Fatal("the org's failures opened the global circuit")
	}

	before := g.ups["pricey"].calls.Load()
	if got := g.chat(t, "m").Header().Get("x-agentcc-provider"); got != "cheap" {
		t.Fatalf("with the org's pricey|m circuit open: routed to %q, want cheap", got)
	}
	if g.ups["pricey"].calls.Load() != before {
		t.Fatal("a request reached the provider whose org circuit is open")
	}

	t.Run("an org failover without its own breaker keeps the global one", func(t *testing.T) {
		o := g.setOrg(t, `{"failover":{"enabled":true,"max_attempts":2}}`)
		if got := h.effectiveFailover(o).Breakers(); got != globalReg {
			t.Fatalf("org failover breakers = %p, want the global registry %p (never nil)", got, globalReg)
		}
	})
}

// Without failover the gateway router itself picks the target, and a global
// cost-optimized strategy ranks by the routed model's price (the router
// hands the model to the strategy).
func TestGlobalCostOptimizedWithoutFailoverReadsPrices(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		cfg.Routing.DefaultStrategy = "cost-optimized"
		cfg.Routing.Failover = config.FailoverConfig{}
	})
	g.srv.handlers.SetInjectedDeps(InjectedDeps{Prices: newOrgRoutePrices().lookup})
	for i := 0; i < 6; i++ {
		if got := g.chat(t, "m").Header().Get("x-agentcc-provider"); got != "cheap" {
			t.Fatalf("request %d under the global cost-optimized strategy, failover off: routed to %q, want cheap", i, got)
		}
	}
}

// A step before failover may rewrite the model a request sends (here the
// global conditional route's model_override) without changing the routed
// model. A failover over the org and gateway routers sends what that step
// chose to every target that has no model override of its own: the routers
// never name a model on a target that configured none.
func TestFailoverKeepsRewrittenRequestModel(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		for id, p := range cfg.Providers {
			p.Models = []string{"m", "m-mini"}
			cfg.Providers[id] = p
		}
		cfg.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{
			Name:      "downgrade",
			Condition: config.ConditionConfig{Field: "model", Op: "$eq", Value: "m"},
			Action:    config.RouteActionConfig{Provider: "cheap", ModelOverride: "m-mini"},
		}}
	})
	g.ups["pricey"].failing.Store(true) // some requests fail over to a second target
	received := func() map[string]int {
		got := map[string]int{}
		for _, u := range g.ups {
			u.mu.Lock()
			for _, m := range u.models {
				got[m]++
			}
			u.models = nil
			u.mu.Unlock()
		}
		return got
	}
	run := func(t *testing.T) {
		t.Helper()
		_ = received()
		for i := 0; i < 6; i++ {
			g.chat(t, "m")
		}
		if got := received(); got["m"] != 0 || got["m-mini"] == 0 {
			t.Fatalf("upstreams received %v, want only m-mini (the conditional route's model_override)", got)
		}
	}

	t.Run("the gateway's own failover", run)
	t.Run("an org's cost_optimized failover", func(t *testing.T) {
		g.srv.handlers.SetInjectedDeps(InjectedDeps{Prices: newOrgRoutePrices().lookup})
		g.setOrg(t, `{"strategy":"cost_optimized"}`)
		run(t)
	})
}

// The org's own failover settings change what a failing request does: which
// statuses move it to another target (failover.on_status_codes, and the
// legacy fallback_enabled with fallback_on_status_codes) and how often the
// same target is retried (retry). A fresh org router round-robins from the
// first target, pricey, which fails every call with a 502. Circuit breaking
// is off, so pricey's failures in one case never refuse it in the next.
func TestOrgFailoverSettingsApplied(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{}
	})
	g.ups["pricey"].failing.Store(true)
	for _, tc := range []struct {
		name, routing string
		status        int
		priceyCalls   int32
	}{
		{"failover on a listed status", `{"failover":{"enabled":true,"max_attempts":3,"on_status_codes":[502]}}`, http.StatusOK, 1},
		{"no failover on an unlisted status", `{"failover":{"enabled":true,"max_attempts":3,"on_status_codes":[503]}}`, http.StatusBadGateway, 1},
		{"legacy fallback on a listed status", `{"fallback_enabled":true,"fallback_on_status_codes":[502]}`, http.StatusOK, 1},
		{"legacy fallback, unlisted status", `{"fallback_enabled":true,"fallback_on_status_codes":[503]}`, http.StatusBadGateway, 1},
		{"retry the same target", `{"failover":{"enabled":true,"max_attempts":1,"on_status_codes":[502]},
			"retry":{"enabled":true,"max_retries":2,"initial_delay":"1ms","max_delay":"2ms","on_status_codes":[502]}}`, http.StatusBadGateway, 3},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g.setOrg(t, tc.routing)
			before := g.ups["pricey"].calls.Load()
			w := g.send("m")
			if w.Code != tc.status {
				t.Fatalf("status %d, body %s; want %d", w.Code, w.Body.String(), tc.status)
			}
			if got := g.ups["pricey"].calls.Load() - before; got != tc.priceyCalls {
				t.Fatalf("pricey served %d calls, want %d", got, tc.priceyCalls)
			}
		})
	}
}

// routingKeyOwners names, for every key of the org routing config, what makes
// it change behaviour at runtime: the behaviour tests that prove it, or the
// feature that implements it. Keys with no owner are in
// routingKeysAwaitingRuling instead.
var routingKeyOwners = map[string]string{
	"strategy":                 "existing: TestOrgStrategyApplied",
	"fallback_enabled":         "existing: TestFallbackAfterOrgProviderUsesTheOrgsKey, TestOrgFailoverSettingsApplied",
	"fallback_on_status_codes": "existing: TestOrgFailoverSettingsApplied",
	"model_fallbacks":          "existing: TestFallbackAfterOrgProviderUsesTheOrgsKey",
	"complexity":               "existing: TestOrgComplexityTier",
	"fastest":                  "feature: fastest response",
	"adaptive":                 "existing: TestOrgStrategyApplied",
	"provider_lock":            "feature: provider lock",
	"failover":                 "existing: TestOrgFailoverSettingsApplied, TestOrgCircuitBreakerHonoured",
	"circuit_breaker":          "existing: TestOrgCircuitBreakerHonoured",
	"retry":                    "existing: TestOrgFailoverSettingsApplied",
	"model_timeouts":           "existing: TestChatCompletion_OrgModelTimeoutApplies",
	"auto":                     "feature: auto routing",
	"data_policy":              "feature: data policy",
	"agents":                   "feature: agent runs",
	"presets":                  "feature: routing presets",
	"idempotency":              "feature: idempotency",
	"prompt_caching":           "feature: prompt caching",
	"max_total_attempts":       "feature: attempt limits",
	"retry_budget":             "feature: retry budget",
	"rollout":                  "feature: staged rollout",
}

// routingKeysAwaitingRuling are parsed and change nothing; no feature owns
// them, and this list may only shrink.
var routingKeysAwaitingRuling = map[string]string{
	"conditional_routes": "parsed, never evaluated (only config.yaml's conditional router runs)",
	"default_model":      "only the org_default_model stamp",
	"scheduled":          "parsed, never read",
	"access_groups":      "parsed; awaiting a ruling",
	"mirror":             "parsed, never read",
}

// featureOwner names the feature that implements a key, alone or before the
// tests that prove it.
var featureOwner = regexp.MustCompile(`^feature: [a-z][a-z ]*(?:: (Test\w+(?:, Test\w+)*))?$`)

// existingOwner names the existing behaviour tests that prove the key.
var existingOwner = regexp.MustCompile(`^existing: (Test\w+(?:, Test\w+)*)$`)

func TestEveryRoutingKeyHasRuntimeOwner(t *testing.T) {
	tests := packageTestNames(t)
	typ := reflect.TypeOf(tenant.RoutingConfig{})
	keys := map[string]bool{}
	for i := 0; i < typ.NumField(); i++ {
		if !typ.Field(i).IsExported() { // state behind a key, e.g. fallbackOff behind fallback_enabled
			continue
		}
		name, _, _ := strings.Cut(typ.Field(i).Tag.Get("json"), ",")
		if name == "" || name == "-" {
			t.Fatalf("RoutingConfig.%s has no json name", typ.Field(i).Name)
		}
		keys[name] = true
		owner, owned := routingKeyOwners[name]
		_, pending := routingKeysAwaitingRuling[name]
		switch {
		case owned && pending:
			t.Errorf("routing.%s is both owned (%q) and awaiting a ruling", name, owner)
		case pending:
			t.Logf("routing.%s awaits a ruling: %s", name, routingKeysAwaitingRuling[name])
		case !owned:
			t.Errorf("routing.%s has no runtime owner: make it change behaviour or name the feature that does", name)
		default:
			m := featureOwner.FindStringSubmatch(owner)
			if m == nil {
				m = existingOwner.FindStringSubmatch(owner)
			}
			if m == nil {
				t.Errorf("routing.%s owner %q names neither a feature nor an existing behaviour test", name, owner)
				continue
			}
			if strings.HasPrefix(owner, "existing") && m[1] == "" {
				t.Errorf("routing.%s names existing tests but lists none", name)
			}
			for _, test := range strings.Split(m[1], ", ") {
				if test != "" && !tests[test] {
					t.Errorf("routing.%s names %s, which is not a test in this package", name, test)
				}
			}
		}
	}
	for _, table := range []map[string]string{routingKeyOwners, routingKeysAwaitingRuling} {
		for name := range table {
			if !keys[name] {
				t.Errorf("routing key %q is listed, but RoutingConfig has no such key", name)
			}
		}
	}
	if len(routingKeysAwaitingRuling) > 5 {
		t.Errorf("%d keys await a ruling; the list may only shrink from 5", len(routingKeysAwaitingRuling))
	}
}

// packageTestNames returns the Test functions declared in this package's
// test files.
func packageTestNames(t *testing.T) map[string]bool {
	t.Helper()
	files, err := filepath.Glob("*_test.go")
	if err != nil || len(files) == 0 {
		t.Fatalf("listing test files: %v (%d)", err, len(files))
	}
	decl := regexp.MustCompile(`(?m)^func (Test\w+)\(t \*testing\.T\)`)
	names := map[string]bool{}
	for _, f := range files {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range decl.FindAllSubmatch(src, -1) {
			names[string(m[1])] = true
		}
	}
	return names
}

// The per-org router is cached by the routing config the request runs under,
// so its hit path must stay cheap (at most 2 allocs).
func TestOrgRoutingCacheHitAllocs(t *testing.T) {
	g := newOrgRouteGateway(t, nil)
	h := g.srv.handlers
	orgCfg := g.setOrg(t, `{"strategy":"cost_optimized","adaptive":{"enabled":false},"complexity":{"enabled":true,"tiers":{"x":{"max_score":100,"model":"m"}}}}`)
	if h.effectiveFailover(orgCfg) == h.failover.Load() {
		t.Fatal("the org router was not built")
	}
	allocs := testing.AllocsPerRun(1000, func() {
		_ = h.orgRouting(orgCfg)
		_ = h.effectiveFailover(orgCfg)
	})
	if allocs > 2 {
		t.Fatalf("per-org router cache hit: %.1f allocs per request, want at most 2", allocs)
	}
}

func BenchmarkOrgRoutingCacheHit(b *testing.B) {
	g := newOrgRouteGateway(b, nil)
	h := g.srv.handlers
	orgCfg := g.setOrg(b, `{"strategy":"cost_optimized"}`)
	_ = h.effectiveFailover(orgCfg)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = h.orgRouting(orgCfg)
		_ = h.effectiveFailover(orgCfg)
	}
}

// Every org circuit for the model open: the request is refused with a 503,
// never dispatched, and never a panic (Failover.Execute returns no result and
// no error when every attempt it had was refused by a circuit).
func TestOrgAllCircuitsOpenRefusedCleanly(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"m": {{Provider: "pricey"}, {Provider: "cheap", Priority: 1}}}
	})
	orgCfg := g.setOrg(t, `{"failover":{"enabled":true,"max_attempts":2,"on_status_codes":[502]},
		"circuit_breaker":{"enabled":true,"failure_threshold":1,"cooldown":"1m"}}`)
	reg := g.srv.handlers.effectiveFailover(orgCfg).Breakers()
	for _, p := range []string{"pricey", "cheap"} {
		reg.Get(routing.BreakerKey(p, "m", "")).RecordFailure(&models.APIError{Status: http.StatusBadGateway})
	}
	before := g.ups["pricey"].calls.Load() + g.ups["cheap"].calls.Load()

	body := `{"model":"m","messages":[{"role":"user","content":"hi"}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-route")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("every circuit open: status %d, body %s; want 503", w.Code, w.Body.String())
	}
	if n := g.ups["pricey"].calls.Load() + g.ups["cheap"].calls.Load(); n != before {
		t.Fatalf("%d upstream calls with every circuit open, want 0", n-before)
	}
}

// The gateway's own failover (no org routing) selects through the same kind
// of router: its strategy is built lazily from the deps in force, so
// cost-optimized reads prices, and every circuit open is a clean 503.
func TestGlobalFailoverStrategyBuiltLazily(t *testing.T) {
	g := newOrgRouteGateway(t, func(cfg *config.Config, _ map[string]*orgRouteUpstream) {
		cfg.Routing.DefaultStrategy = "cost-optimized"
		cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Minute}
	})
	h := g.srv.handlers
	if got := h.effectiveFailover(nil); got == nil || got == h.failover.Load() || got.Breakers() != h.failover.Load().Breakers() {
		t.Fatalf("effectiveFailover(nil) = %p (global %p), want the gateway's strategy router with the global breakers", got, h.failover.Load())
	}
	prices := newOrgRoutePrices()
	h.SetInjectedDeps(InjectedDeps{Prices: prices.lookup})
	for i := 0; i < 6; i++ {
		if got := g.chat(t, "m").Header().Get("x-agentcc-provider"); got != "cheap" {
			t.Fatalf("request %d under the global cost-optimized strategy went to %q, want cheap", i, got)
		}
	}

	reg := h.failover.Load().Breakers()
	for _, p := range []string{"cheap", "mid", "pricey"} {
		reg.Get(routing.BreakerKey(p, "m", "")).RecordFailure(&models.APIError{Status: http.StatusBadGateway})
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"m","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-route")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusServiceUnavailable || !strings.Contains(w.Body.String(), "all_providers_unavailable") {
		t.Fatalf("every global circuit open: status %d, body %s; want 503 all_providers_unavailable", w.Code, w.Body.String())
	}
}
