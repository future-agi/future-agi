package routing

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Real adaptive, price-ranked cost-optimized, and circuits per model or
// endpoint. Circuits are opened through the registry with BreakerKey keys
// (record and check under the same key), the keys the failover record site
// uses in a running gateway.

// fakePrices is a PriceLookup over a mutable table, so a test can change a
// price the way a catalog refresh does.
type fakePrices struct {
	mu    sync.Mutex
	cards map[string]models.PriceCard // "provider|model"
}

func newFakePrices(cards map[string]models.PriceCard) *fakePrices {
	return &fakePrices{cards: cards}
}

func (f *fakePrices) set(provider, model string, card models.PriceCard) {
	f.mu.Lock()
	f.cards[provider+"|"+model] = card
	f.mu.Unlock()
}

func (f *fakePrices) lookup(providerID, model, _ string) (models.PriceCard, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	c, ok := f.cards[providerID+"|"+model]
	return c, ok
}

// flat is a card whose prompt and completion prices are both usd per token.
func flat(usd float64) models.PriceCard {
	return models.PriceCard{Prompt: usd, Completion: usd, Source: models.PriceSourceCatalog}
}

// fakeModelHealth reports fixed per-(provider, model) snapshots.
type fakeModelHealth map[string]ModelHealthSnapshot

func (f fakeModelHealth) Snapshot(providerID, model string) ModelHealthSnapshot {
	return f[providerID+"|"+model]
}

func strategyRouter(t *testing.T, s Strategy, targets map[string][]config.RoutingTargetConfig) *Router {
	t.Helper()
	ids := map[string]bool{}
	for _, ts := range targets {
		for _, tc := range ts {
			ids[tc.Provider] = true
		}
	}
	base, err := NewRouter(config.RoutingConfig{DefaultStrategy: "round-robin", Targets: targets}, ids, nil)
	if err != nil {
		t.Fatalf("NewRouter: %v", err)
	}
	return NewStrategyRouter(base, s)
}

func status500() error {
	return &models.APIError{Status: 500, Type: models.ErrTypeServer, Code: "upstream_error", Message: "boom"}
}

// served runs one failover over fo for model and returns the provider that
// served it (every call succeeds).
func served(t *testing.T, fo *Failover, model string) string {
	t.Helper()
	var got string
	_, err := fo.Execute(context.Background(), model, func(_ context.Context, providerID, _ string) error {
		got = providerID
		return nil
	})
	if err != nil {
		t.Fatalf("Execute(%s): %v", model, err)
	}
	return got
}

func TestAdaptiveFactoryNotRoundRobin(t *testing.T) {
	targets := []RoutingTarget{
		{ProviderID: "slow", Healthy: true, ModelOverride: "m"},
		{ProviderID: "fast", Healthy: true, ModelOverride: "m"},
	}

	for _, name := range []string{"adaptive", "Adaptive"} {
		s, err := NewStrategy(name)
		if err != nil {
			t.Fatalf("NewStrategy(%q): %v", name, err)
		}
		if _, ok := s.(*AdaptiveStrategy); !ok || s.Name() != "adaptive" {
			t.Fatalf("NewStrategy(%q) = %T named %q, want *AdaptiveStrategy named adaptive", name, s, s.Name())
		}
	}

	t.Run("learning round-robins until it has samples", func(t *testing.T) {
		s, _ := NewStrategy("adaptive", StrategyDeps{ModelHealth: fakeModelHealth{}})
		seen := map[int]int{}
		for i := 0; i < 10; i++ {
			idx, err := s.Select(targets, nil)
			if err != nil {
				t.Fatal(err)
			}
			seen[idx]++
		}
		if seen[0] != 5 || seen[1] != 5 {
			t.Fatalf("selections without samples = %v, want an even round-robin", seen)
		}
	})

	t.Run("after 50 samples it prefers the lower-latency target (per-model health)", func(t *testing.T) {
		health := fakeModelHealth{
			"slow|m": {N: 50, SuccessRate: 1, P50Ms: 400},
			"fast|m": {N: 50, SuccessRate: 1, P50Ms: 40},
		}
		s, _ := NewStrategy("adaptive", StrategyDeps{ModelHealth: health})
		if fast := share(t, s, targets, 1, 4000); fast < 0.9 {
			t.Fatalf("fast target share = %.3f, want >= 0.9", fast)
		}
	})

	t.Run("after 50 samples it prefers the lower-latency target (latency EWMA)", func(t *testing.T) {
		tracker := NewLatencyTracker(0.3)
		hm := NewHealthMonitor(tracker, nil)
		for i := 0; i < 50; i++ {
			tracker.Record("slow", 400*time.Millisecond)
			tracker.Record("fast", 40*time.Millisecond)
			hm.RecordSuccess("slow")
			hm.RecordSuccess("fast")
		}
		s, _ := NewStrategy("adaptive", StrategyDeps{Latency: tracker, Health: hm})
		if fast := share(t, s, targets, 1, 4000); fast < 0.9 {
			t.Fatalf("fast target share = %.3f, want >= 0.9", fast)
		}
	})

	t.Run("errors outweigh latency", func(t *testing.T) {
		health := fakeModelHealth{
			"slow|m": {N: 50, SuccessRate: 1, P50Ms: 400},
			"fast|m": {N: 50, SuccessRate: 0.2, ErrorRate: 0.8, P50Ms: 40},
		}
		s, _ := NewStrategy("adaptive", StrategyDeps{ModelHealth: health})
		if healthy := share(t, s, targets, 0, 4000); healthy < 0.9 {
			t.Fatalf("healthy slow target share = %.3f, want >= 0.9 over a fast target failing 80%%", healthy)
		}
	})

	t.Run("health is read per model, not per provider", func(t *testing.T) {
		health := fakeModelHealth{
			"p|m-slow": {N: 50, SuccessRate: 1, P50Ms: 900},
			"p|m-fast": {N: 50, SuccessRate: 1, P50Ms: 30},
		}
		s, _ := NewStrategy("adaptive", StrategyDeps{ModelHealth: health})
		perModel := []RoutingTarget{
			{ProviderID: "p", Healthy: true, ModelOverride: "m-slow"},
			{ProviderID: "p", Healthy: true, ModelOverride: "m-fast"},
		}
		if fast := share(t, s, perModel, 1, 4000); fast < 0.9 {
			t.Fatalf("fast model share = %.3f, want >= 0.9", fast)
		}
	})
}

// share selects n times and returns the fraction of selections that picked want.
func share(t *testing.T, s Strategy, targets []RoutingTarget, want, n int) float64 {
	t.Helper()
	hits := 0
	for i := 0; i < n; i++ {
		idx, err := s.Select(targets, nil)
		if err != nil {
			t.Fatal(err)
		}
		if idx == want {
			hits++
		}
	}
	return float64(hits) / float64(n)
}

func TestCostOptimizedUsesPrice(t *testing.T) {
	prices := newFakePrices(map[string]models.PriceCard{
		"a|m": flat(3e-6),
		"b|m": flat(1e-6),
		"c|m": flat(2e-6),
	})
	s, err := NewStrategy("cost_optimized", StrategyDeps{Prices: prices.lookup})
	if err != nil {
		t.Fatal(err)
	}
	if s.Name() != "cost-optimized" {
		t.Fatalf("cost_optimized is named %q, want cost-optimized", s.Name())
	}
	r := strategyRouter(t, s, map[string][]config.RoutingTargetConfig{
		"m": {{Provider: "a"}, {Provider: "b"}, {Provider: "c"}},
	})

	t.Run("the cheapest target is chosen every time while healthy", func(t *testing.T) {
		for i := 0; i < 100; i++ {
			res, err := r.Select("m")
			if err != nil {
				t.Fatal(err)
			}
			if res.Target.ProviderID != "b" {
				t.Fatalf("selection %d went to %q, want b (price 1 of 3, 1, 2)", i, res.Target.ProviderID)
			}
		}
	})

	t.Run("the next cheapest once the cheapest circuit opens", func(t *testing.T) {
		reg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: time.Minute}, nil)
		fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, r, nil, reg)
		if got := served(t, fo, "m"); got != "b" {
			t.Fatalf("closed circuits: served by %q, want b", got)
		}
		cb := reg.Get(BreakerKey("b", "m", ""))
		cb.RecordFailure(status500())
		cb.RecordFailure(status500())
		if cb.State() != StateOpen {
			t.Fatalf("b|m circuit = %v after 2 failures, want open", cb.State())
		}
		for i := 0; i < 20; i++ {
			if got := served(t, fo, "m"); got != "c" {
				t.Fatalf("request %d with b open: served by %q, want c (the next cheapest)", i, got)
			}
		}
	})

	t.Run("blended price weighs input three to one", func(t *testing.T) {
		// (3*1 + 9) / 4 = 3 against (3*2.5 + 2.5) / 4 = 2.5: the second is
		// cheaper although its input price is higher.
		blend := newFakePrices(map[string]models.PriceCard{
			"x|m": {Prompt: 1e-6, Completion: 9e-6},
			"y|m": {Prompt: 2.5e-6, Completion: 2.5e-6},
		})
		if got, want := BlendedPrice(models.PriceCard{Prompt: 1, Completion: 9}), 3.0; got != want {
			t.Fatalf("BlendedPrice = %v, want %v", got, want)
		}
		cs, _ := NewStrategy("cost-optimized", StrategyDeps{Prices: blend.lookup})
		idx, err := cs.Select([]RoutingTarget{{ProviderID: "x", ModelOverride: "m"}, {ProviderID: "y", ModelOverride: "m"}}, nil)
		if err != nil || idx != 1 {
			t.Fatalf("Select = %d, %v; want 1 (y, blended 2.5 < 3)", idx, err)
		}
	})

	t.Run("prices are read at selection time", func(t *testing.T) {
		prices.set("c", "m", flat(0.5e-6))
		defer prices.set("c", "m", flat(2e-6))
		res, err := r.Select("m")
		if err != nil || res.Target.ProviderID != "c" {
			t.Fatalf("after c's price fell to 0.5: Select = %+v, %v; want c", res, err)
		}
	})

	t.Run("ties fall back to priority, then round-robin", func(t *testing.T) {
		tie := newFakePrices(map[string]models.PriceCard{"p|m": flat(1e-6), "q|m": flat(1e-6), "z|m": flat(1e-6)})
		cs, _ := NewStrategy("cost-optimized", StrategyDeps{Prices: tie.lookup})
		ts := []RoutingTarget{
			{ProviderID: "p", Priority: 2, ModelOverride: "m"},
			{ProviderID: "q", Priority: 1, ModelOverride: "m"},
			{ProviderID: "z", Priority: 1, ModelOverride: "m"},
		}
		got := map[int]int{}
		for i := 0; i < 6; i++ {
			idx, _ := cs.Select(ts, nil)
			got[idx]++
		}
		if got[0] != 0 || got[1] != 3 || got[2] != 3 {
			t.Fatalf("selections = %v, want q and z (priority 1) alternating, never p", got)
		}
	})

	t.Run("a model-aware caller prices targets without an override", func(t *testing.T) {
		ms, ok := s.(ModelStrategy)
		if !ok {
			t.Fatalf("%T does not implement ModelStrategy", s)
		}
		idx, err := ms.SelectModel("m", []RoutingTarget{{ProviderID: "a"}, {ProviderID: "b"}, {ProviderID: "c"}}, nil)
		if err != nil || idx != 1 {
			t.Fatalf("SelectModel(m) = %d, %v; want 1 (b)", idx, err)
		}
	})
}

func TestCostOptimizedUnpricedLast(t *testing.T) {
	prices := newFakePrices(map[string]models.PriceCard{"priced|m": flat(5e-6)})
	s, _ := NewStrategy("cost-optimized", StrategyDeps{Prices: prices.lookup})
	targets := []RoutingTarget{
		{ProviderID: "unpriced", Priority: 0, ModelOverride: "m"},
		{ProviderID: "priced", Priority: 9, ModelOverride: "m"},
	}
	for i := 0; i < 10; i++ {
		if idx, _ := s.Select(targets, nil); idx != 1 {
			t.Fatalf("selection %d = %d, want the priced target although its priority is worse", i, idx)
		}
	}
	if idx, _ := s.Select(targets[:1], nil); idx != 0 {
		t.Fatalf("only an unpriced target left: Select = %d, want 0", idx)
	}

	// Through a failover: the unpriced target is tried only after the priced one fails.
	r := strategyRouter(t, s, map[string][]config.RoutingTargetConfig{
		"m": {{Provider: "unpriced"}, {Provider: "priced", Priority: 9}},
	})
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{500}}, r, nil, nil)
	var order []string
	_, err := fo.Execute(context.Background(), "m", func(_ context.Context, providerID, _ string) error {
		order = append(order, providerID)
		if providerID == "priced" {
			return status500()
		}
		return nil
	})
	if err != nil || fmt.Sprint(order) != "[priced unpriced]" {
		t.Fatalf("failover order = %v (err %v), want [priced unpriced]", order, err)
	}

	t.Run("no prices at all: priority, then round-robin", func(t *testing.T) {
		none, _ := NewStrategy("cost-optimized")
		ts := []RoutingTarget{{ProviderID: "x", Priority: 3}, {ProviderID: "y", Priority: 1}}
		if idx, _ := none.Select(ts, nil); idx != 1 {
			t.Fatalf("Select = %d, want 1 (priority 1)", idx)
		}
	})
}

func TestOrgCircuitBreakerIsolated(t *testing.T) {
	var flips []string
	var flipMu sync.Mutex
	onChange := func(id string, healthy bool) {
		flipMu.Lock()
		flips = append(flips, fmt.Sprintf("%s=%v", id, healthy))
		flipMu.Unlock()
	}
	global := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true}, onChange) // threshold 5
	orgA := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2}, nil)
	orgB := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2}, nil)

	key := BreakerKey("p1", "m", "")
	for i := 0; i < 2; i++ {
		orgA.Get(key).RecordFailure(status500())
	}
	if orgA.Get(key).State() != StateOpen {
		t.Fatalf("org A %s = %v after 2 failures at threshold 2, want open", key, orgA.Get(key).State())
	}
	if s := orgB.Get(key).State(); s != StateClosed {
		t.Fatalf("org B %s = %v, want closed: another org's failures must not open it", key, s)
	}
	if s := global.Get(key).State(); s != StateClosed {
		t.Fatalf("global %s = %v, want closed", key, s)
	}

	// Both orgs route the same base router; only org A avoids p1.
	cost, _ := NewStrategy("cost-optimized")
	r := strategyRouter(t, cost, map[string][]config.RoutingTargetConfig{"m": {{Provider: "p1"}, {Provider: "p2", Priority: 1}}})
	foA := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, r, nil, orgA)
	foB := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, r, nil, orgB)
	if got := served(t, foA, "m"); got != "p2" {
		t.Fatalf("org A served by %q, want p2 (its p1|m circuit is open)", got)
	}
	if got := served(t, foB, "m"); got != "p1" {
		t.Fatalf("org B served by %q, want p1", got)
	}

	// A per-model circuit never flips provider-wide health.
	for i := 0; i < 5; i++ {
		global.Get(key).RecordFailure(status500())
	}
	if global.Get(key).State() != StateOpen {
		t.Fatal("global p1|m did not open after 5 failures")
	}
	flipMu.Lock()
	defer flipMu.Unlock()
	if len(flips) != 0 {
		t.Fatalf("onChange fired %v for a per-model circuit, want no provider-wide flip", flips)
	}
}

func TestManagedModelCircuitIsolated(t *testing.T) {
	reg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 5, Cooldown: time.Minute}, nil)
	failing := reg.Get(BreakerKey("openrouter", "mock/fail-500", ""))
	for i := 0; i < 25; i++ {
		failing.RecordFailure(status500())
	}
	if failing.State() != StateOpen || failing.Allow() {
		t.Fatalf("mock/fail-500 circuit = %v after 25 failures, want open and refusing", failing.State())
	}
	other := reg.Get(BreakerKey("openrouter", "mock/ok-1", ""))
	if other.State() != StateClosed || !other.Allow() {
		t.Fatalf("mock/ok-1 circuit on the same provider = %v, want closed", other.State())
	}

	cost, _ := NewStrategy("cost-optimized")
	r := strategyRouter(t, cost, map[string][]config.RoutingTargetConfig{
		"mock/fail-500": {{Provider: "openrouter"}, {Provider: "backup", Priority: 1}},
		"mock/ok-1":     {{Provider: "openrouter"}, {Provider: "backup", Priority: 1}},
	})
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, r, nil, reg)
	if got := served(t, fo, "mock/ok-1"); got != "openrouter" {
		t.Fatalf("mock/ok-1 served by %q, want openrouter: one model's open circuit must not close the provider", got)
	}
	if got := served(t, fo, "mock/fail-500"); got != "backup" {
		t.Fatalf("mock/fail-500 served by %q, want backup (its openrouter circuit is open)", got)
	}

	// The provider view aggregates its models: partly open reads half-open
	// (traffic still flows), and open only once every model's circuit is.
	hm := NewHealthMonitor(nil, reg)
	if h := hm.GetHealth("openrouter"); h.CircuitState != "half-open" || !h.Healthy {
		t.Fatalf("provider view with one of two models open = %q healthy=%v, want half-open, healthy", h.CircuitState, h.Healthy)
	}
	for i := 0; i < 5; i++ {
		other.RecordFailure(status500())
	}
	if h := hm.GetHealth("openrouter"); h.CircuitState != "open" || h.Healthy {
		t.Fatalf("provider view with every model open = %q healthy=%v, want open, unhealthy", h.CircuitState, h.Healthy)
	}
}

// An org's router takes the gateway's targets, never a provider-wide health
// flag of the moment: it would keep it until the org's config changed. Its
// failover excludes failing targets through its circuits.
func TestStrategyRouterDoesNotFreezeProviderHealth(t *testing.T) {
	base, err := NewRouter(config.RoutingConfig{DefaultStrategy: "round-robin",
		Targets: map[string][]config.RoutingTargetConfig{"m": {{Provider: "a"}, {Provider: "b"}}}},
		map[string]bool{"a": true, "b": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	base.SetHealthy("a", false)
	cost, _ := NewStrategy("cost-optimized")
	r := NewStrategyRouter(base, cost)
	base.SetHealthy("a", true)
	seen := map[string]bool{}
	for i := 0; i < 4; i++ {
		res, err := r.Select("m")
		if err != nil {
			t.Fatal(err)
		}
		seen[res.Target.ProviderID] = true
	}
	if !seen["a"] {
		t.Fatalf("the org router never selects a, which was unhealthy only while it was built: %v", seen)
	}
}

// The failover's call sends a target's model override in place of the model
// the request carries, so a strategy router names no model on a target that
// configured none (a step before failover may have rewritten the request's
// model) and keeps the one a target configured.
func TestStrategyRouterKeepsConfiguredOverrides(t *testing.T) {
	rr, _ := NewStrategy("round-robin")
	r := strategyRouter(t, rr, map[string][]config.RoutingTargetConfig{
		"m": {{Provider: "a"}, {Provider: "b", ModelOverride: "m-on-b"}},
	})
	got := map[string]string{}
	for i := 0; i < 4; i++ {
		res, err := r.Select("m")
		if err != nil {
			t.Fatal(err)
		}
		got[res.Target.ProviderID] = res.Target.ModelOverride
	}
	if want := map[string]string{"a": "", "b": "m-on-b"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("selected targets' model overrides = %v, want %v", got, want)
	}
}

// A failover that keeps the gateway's strategy selects with the gateway
// router's kind of strategy, reading the deps it is given, and an adaptive
// one keeps the tuning the gateway's was built with.
func TestStrategyLikeKeepsGatewayTuning(t *testing.T) {
	prices := newFakePrices(map[string]models.PriceCard{"a|m": flat(2e-6), "b|m": flat(1e-6)})
	deps := StrategyDeps{Prices: prices.lookup, Adaptive: config.AdaptiveConfig{LearningRequests: 99}}

	base, err := NewRouter(config.RoutingConfig{DefaultStrategy: "cost-optimized"}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	s, err := StrategyLike(base, deps)
	if err != nil || s.Name() != StrategyCostOptimized {
		t.Fatalf("StrategyLike(cost-optimized router) = %v, %v", s, err)
	}
	if idx, err := s.(ModelStrategy).SelectModel("m", []RoutingTarget{{ProviderID: "a"}, {ProviderID: "b"}}, nil); err != nil || idx != 1 {
		t.Fatalf("its SelectModel = %d, %v; want 1 (b, priced from the deps given)", idx, err)
	}

	base.defaultStrategy = NewAdaptiveStrategy(config.AdaptiveConfig{LearningRequests: 7, MinWeight: 0.2,
		SignalWeights: config.SignalWeightsConfig{Latency: 0.9, ErrorRate: 0.1}}, nil)
	s, err = StrategyLike(base, deps)
	a, ok := s.(*AdaptiveStrategy)
	if err != nil || !ok {
		t.Fatalf("StrategyLike(adaptive router) = %T, %v", s, err)
	}
	if a.learning != 7 || a.explore != 0.2 || a.latencyWeight != 0.9 || a.errorWeight != 0.1 {
		t.Fatalf("tuning = learning %d explore %v weights %v/%v; want the gateway's 7, 0.2, 0.9/0.1 (not deps.Adaptive)",
			a.learning, a.explore, a.latencyWeight, a.errorWeight)
	}
	if a.deps.get().Prices == nil || base.defaultStrategy.(*AdaptiveStrategy).deps.get().Prices != nil {
		t.Fatal("the new strategy must read the deps given, and the gateway's must stay as it was")
	}
}

// The gateway router hands a model-aware strategy the routed model, so it
// prices a target with no model override, and builds adaptive with
// config.yaml's routing.adaptive tuning, its deps still bound after startup.
func TestGatewayRouterIsModelAwareAndTuned(t *testing.T) {
	prices := newFakePrices(map[string]models.PriceCard{"a|m": flat(2e-6), "b|m": flat(1e-6)})
	r, err := NewRouter(config.RoutingConfig{DefaultStrategy: "cost-optimized",
		Targets: map[string][]config.RoutingTargetConfig{"m": {{Provider: "a"}, {Provider: "b"}}}},
		map[string]bool{"a": true, "b": true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !BindStrategyDeps(r, func() StrategyDeps { return StrategyDeps{Prices: prices.lookup} }) {
		t.Fatal("the config-time cost-optimized strategy did not await its deps")
	}
	for i := 0; i < 4; i++ {
		if res, err := r.Select("m"); err != nil || res.Target.ProviderID != "b" {
			t.Fatalf("selection %d = %+v, %v; want b (price 1 of 2, 1), priced for the routed model", i, res, err)
		}
	}

	ar, err := NewRouter(config.RoutingConfig{DefaultStrategy: "adaptive",
		Adaptive: config.AdaptiveConfig{LearningRequests: 7, MinWeight: 0.2}}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	a, ok := ar.defaultStrategy.(*AdaptiveStrategy)
	if !ok || a.learning != 7 || a.explore != 0.2 {
		t.Fatalf("gateway adaptive = %T %+v; want learning_requests 7 and min_weight 0.2 from routing.adaptive", ar.defaultStrategy, a)
	}
	if !StrategyAwaitsDeps(ar) {
		t.Fatal("the tuned adaptive strategy must still await its deps")
	}
}

func TestBreakerKeyPerModelAndEndpoint(t *testing.T) {
	for _, tc := range []struct{ provider, model, endpoint, want string }{
		{"openai", "gpt-4o", "", "openai|gpt-4o"},
		{"openrouter", "anthropic/claude", "", "openrouter|anthropic/claude"},
		{"openrouter", "anthropic/claude", "openrouter:anthropic:anthropic/claude", "openrouter|openrouter:anthropic:anthropic/claude"},
		{"p", "", "", "p"},
	} {
		if got := BreakerKey(tc.provider, tc.model, tc.endpoint); got != tc.want {
			t.Errorf("BreakerKey(%q, %q, %q) = %q, want %q", tc.provider, tc.model, tc.endpoint, got, tc.want)
		}
	}

	reg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1}, nil)
	a := reg.Get(BreakerKey("openrouter", "m1", ""))
	b := reg.Get(BreakerKey("openrouter", "m2", ""))
	e1 := reg.Get(BreakerKey("openrouter", "m1", "openrouter:x:m1"))
	e2 := reg.Get(BreakerKey("openrouter", "m1", "openrouter:y:m1"))
	if a == b || a == e1 || e1 == e2 {
		t.Fatal("distinct models or endpoints share a breaker")
	}
	if reg.Get(BreakerKey("openrouter", "m1", "")) != a {
		t.Fatal("the same key returned a different breaker")
	}
	e1.RecordFailure(errors.New("network down"))
	if e1.State() != StateOpen || e2.State() != StateClosed || a.State() != StateClosed {
		t.Fatalf("after one endpoint failed: e1=%v e2=%v model=%v, want only e1 open", e1.State(), e2.State(), a.State())
	}
	if reg.Get("openrouter").State() != StateHalfOpen {
		t.Fatalf("provider view = %v, want half-open with one of four circuits open", reg.Get("openrouter").State())
	}
	if reg.Get("other").State() != StateClosed {
		t.Fatal("a provider with no circuits must read closed")
	}
}

// A strategy router over circuits never offers a target whose circuit
// refuses while another serves the model, so the failover spends no attempt
// on it; with every circuit refusing the run ends with a 503, not with no
// result and no error.
func TestSkipOpenCircuits(t *testing.T) {
	reg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Minute}, nil)
	rr, _ := NewStrategy("round-robin")
	r := strategyRouter(t, SkipOpenCircuits(rr, reg), map[string][]config.RoutingTargetConfig{
		"m": {{Provider: "a"}, {Provider: "b"}, {Provider: "c"}},
	})
	reg.Get(BreakerKey("a", "m", "")).RecordFailure(status500())
	for i := 0; i < 6; i++ {
		res, err := r.Select("m")
		if err != nil || res.Target.ProviderID == "a" {
			t.Fatalf("selection %d = %+v, %v; want b or c while a|m is open", i, res, err)
		}
	}

	// MaxAttempts 1: the one attempt goes to a serving target, never to a skip.
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, r, nil, reg)
	for i := 0; i < 4; i++ {
		if got := served(t, fo, "m"); got == "a" {
			t.Fatalf("request %d served by a, whose circuit is open", i)
		}
	}

	reg.Get(BreakerKey("b", "m", "")).RecordFailure(status500())
	reg.Get("c").RecordFailure(status500()) // a caller recording by provider id refuses all of c
	calls := 0
	res, err := fo.Execute(context.Background(), "m", func(context.Context, string, string) error { calls++; return nil })
	var apiErr *models.APIError
	if res != nil || !errors.As(err, &apiErr) || apiErr.Status != 503 || apiErr.Code != "all_providers_unavailable" || calls != 0 {
		t.Fatalf("every circuit refusing: Execute = %+v, %v after %d calls; want no result, a 503 all_providers_unavailable, 0 calls", res, err, calls)
	}

	t.Run("an open circuit past its cooldown is offered for its probe", func(t *testing.T) {
		short := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Millisecond}, nil)
		cb := short.Get(BreakerKey("a", "m", ""))
		cb.RecordFailure(status500())
		time.Sleep(5 * time.Millisecond)
		if short.Refuses("a", "m") || cb.State() != StateOpen {
			t.Fatalf("past its cooldown: Refuses = %v, state %v; want not refusing, still open until Allow", short.Refuses("a", "m"), cb.State())
		}
	})

	t.Run("off: the strategy is returned as is", func(t *testing.T) {
		if got := SkipOpenCircuits(rr, NewCircuitBreakerRegistry(config.CircuitBreakerConfig{}, nil)); got != rr {
			t.Fatalf("SkipOpenCircuits with breaking off = %T, want the strategy itself", got)
		}
		if got := SkipOpenCircuits(rr, nil); got != rr {
			t.Fatalf("SkipOpenCircuits(nil registry) = %T, want the strategy itself", got)
		}
	})
}
