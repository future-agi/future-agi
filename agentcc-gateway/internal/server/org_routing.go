package server

import (
	"container/list"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An org's routing config changes what the gateway does, not only what it
// stamps. Its strategy, adaptive and circuit_breaker settings
// build the org a router of its own over the gateway's targets, which its
// failover selects through; its complexity settings map an explicit request
// to a tier's model. These per-org instances are built lazily, on first use
// after startup, from the injected deps in force then, and cached by the
// routing config the request runs under (orgRoutings).

// effectiveFailover returns the failover a request runs: the org's own when
// its routing config builds one, else the gateway's. Both select through a
// strategy router built lazily from the deps in force, which skips targets
// whose circuit is open; the global failover itself serves only when there
// is no gateway router to select through.
func (h *Handlers) effectiveFailover(orgCfg *tenant.OrgConfig) *routing.Failover {
	if o := h.orgRouting(orgCfg); o != nil && o.failover != nil {
		return o.failover
	}
	if g := h.globalRouting(); g != nil {
		return g.failover
	}
	return h.failover.Load()
}

// globalRouting is the gateway's own routing instance: the global failover's
// settings and breakers over a strategy router with the gateway's strategy,
// nil without a global failover or a gateway router. Every failover request
// reads it, so it is kept per Handlers in a sync.Map (no lock on the read)
// rather than in the org LRU; a reload or new deps replace it.
func (h *Handlers) globalRouting() *orgRouting {
	gr, global := h.registry.Router(), h.failover.Load()
	if gr == nil || global == nil {
		return nil
	}
	deps := h.Deps()
	if v, ok := globalRoutings.Load(h); ok {
		if o := v.(*orgRouting); o.builtFrom(gr, global, deps) {
			return o
		}
	}
	cfg, retryer := global.Settings()
	s, _ := h.orgStrategy(&tenant.RoutingConfig{}, gr, deps)
	o := &orgRouting{router: gr, global: global, deps: deps}
	o.failover = routing.NewFailover(cfg, routing.NewStrategyRouter(gr, routing.SkipOpenCircuits(s, global.Breakers())), retryer, global.Breakers())
	globalRoutings.Store(h, o)
	return o
}

// globalRoutings holds each Handlers' own routing instance (see orgRoutings
// for why it is not a Handlers field).
var globalRoutings sync.Map // *Handlers -> *orgRouting

// applyOrgRoutingOverrides applies per-org routing config to the request
// context: the org's stamps, the strategy its router applies, and its
// complexity tier mapping (call-order step 4, before resolveRoute).
func (h *Handlers) applyOrgRoutingOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	h.bindGlobalStrategy()
	if orgCfg == nil || orgCfg.Routing == nil {
		return
	}

	r := orgCfg.Routing

	// If the org has a default model and the request model isn't explicitly
	// mapped in the registry, use the org's default as a hint.
	if r.DefaultModel != "" {
		rc.Metadata["org_default_model"] = r.DefaultModel
	}

	// Record the org's preferred routing strategy for observability.
	if r.Strategy != "" {
		rc.Metadata["org_routing_strategy"] = r.Strategy
	}

	// Apply per-org model fallbacks: store in metadata for downstream use.
	// An org that turned its chains off (fallback_enabled false) keeps them
	// stored but never tried (FallbackOn).
	if len(r.ModelFallbacks) > 0 && r.FallbackOn() {
		rc.Metadata["org_has_fallbacks"] = "true"
		rc.OrgModelFallbacks = r.ModelFallbacks
	}

	// Advanced routing: complexity routing override.
	if r.Complexity != nil && r.Complexity.Enabled {
		rc.Metadata["org_complexity_routing"] = "true"
	}

	// Advanced routing: fastest response / race mode.
	if r.Fastest != nil && r.Fastest.Enabled {
		rc.Metadata["org_fastest_response"] = "true"
	}

	// Advanced routing: adaptive routing.
	if r.Adaptive != nil && r.Adaptive.Enabled {
		rc.Metadata["org_adaptive_routing"] = "true"
	}

	// Advanced routing: provider lock.
	if r.ProviderLock != nil && r.ProviderLock.Enabled {
		rc.Metadata["org_provider_lock"] = "true"
	}

	// Reliability: circuit breaker.
	if r.CircuitBreaker != nil && r.CircuitBreaker.Enabled {
		rc.Metadata["org_circuit_breaker"] = "true"
	}

	// Reliability: retry.
	if r.Retry != nil && r.Retry.Enabled {
		rc.Metadata["org_retry"] = "true"
	}

	o := h.orgRouting(orgCfg)
	if o == nil {
		return
	}
	h.applyOrgComplexity(o.complexity, rc)
	if o.strategy != "" && h.orgRouterSelects(o, rc) {
		rc.Metadata["routing_strategy_applied"] = o.strategy
		rc.Metadata[routing.RouteReasonStrategy] = o.strategy
	}
}

// orgRouterSelects reports whether the org's router will choose rc's target:
// its failover runs, the gateway router has targets for the model, and the
// key may use the gateway's providers (useProviderFailover's test). A
// customer key never fails over across config.yaml providers, so it is not
// stamped with a strategy that never ran; nor is a licence-authorized
// request, which resolveRoute recognises only after this step.
func (h *Handlers) orgRouterSelects(o *orgRouting, rc *models.RequestContext) bool {
	return o.failover.IsEnabled() && o.router.HasTargets(rc.Model) &&
		(h.keyStore == nil || rc.Metadata["key_type"] == "internal")
}

// applyOrgComplexity maps a chat request for a bare model to the org's
// complexity tier: the tier the request names in x-agentcc-complexity-override
// when the org has one by that name, else the tier its prompt scores. Auto,
// provider-locked and provider-qualified requests keep their model, as with
// the global complexity routing in resolveRoute.
func (h *Handlers) applyOrgComplexity(a *routing.ComplexityAnalyzer, rc *models.RequestContext) {
	if !a.IsEnabled() || rc.EndpointType != "chat" || rc.Request == nil ||
		routing.IsAutoModel(rc.Model) || strings.Contains(rc.Model, "/") {
		return
	}
	if h.providerLockResolver.IsEnabled() && h.providerLockResolver.ExtractLock(rc, rc.RequestHeaders) != "" {
		return
	}
	override := rc.RequestHeaders.Get("x-agentcc-complexity-override")
	result, explicit := a.Tier(override)
	if !explicit {
		result = a.Analyze(rc.Request)
	}
	if result.Model == "" {
		return
	}
	original := rc.Model
	rc.Model = result.Model
	rc.Request.Model = result.Model
	routing.SetComplexityMetadata(rc, result, original)
	rc.SetMetadata("complexity_source", "org")
	if explicit {
		rc.SetMetadata("complexity_override", override)
	}
	if result.Provider != "" {
		rc.SetMetadata("complexity_provider", result.Provider)
	}
}

// bindGlobalStrategy gives the gateway router's strategy, built from
// config.yaml before the deps existed, a source of deps it reads at every
// selection. It binds once; after that it is a lock and a pointer check.
func (h *Handlers) bindGlobalStrategy() {
	gr := h.registry.Router()
	if !routing.StrategyAwaitsDeps(gr) {
		return
	}
	routing.BindStrategyDeps(gr, func() routing.StrategyDeps {
		d := h.Deps()
		return routing.StrategyDeps{Latency: gr.GetLatencyTracker(), Health: h.healthMonitor, ModelHealth: d.ModelHealth, Prices: d.Prices}
	})
}

// orgRouting is what an org's routing config builds.
type orgRouting struct {
	// What it was built from: the gateway router, the global failover and the
	// injected deps. A change to any of them rebuilds it.
	router *routing.Router
	global *routing.Failover
	deps   *InjectedDeps

	// failover selects through the org's router with its circuit breakers;
	// nil when the global failover serves the org.
	failover *routing.Failover
	// strategy is the canonical name of the strategy the org chose, "" when
	// its router keeps the gateway's.
	strategy   string
	complexity *routing.ComplexityAnalyzer
}

// orgRouting returns the org's routing instance, nil when its config changes
// nothing about selection or complexity. The hit path takes the cache lock
// and allocates nothing.
func (h *Handlers) orgRouting(orgCfg *tenant.OrgConfig) *orgRouting {
	if orgCfg == nil || orgCfg.Routing == nil {
		return nil
	}
	r := orgCfg.Routing
	if !orgSelects(r) && !orgComplexity(r) {
		return nil
	}
	gr, global, deps := h.registry.Router(), h.failover.Load(), h.Deps()
	key := orgRoutingKey{h: h, routing: r}
	if o := orgRoutings.get(key); o != nil && o.builtFrom(gr, global, deps) {
		return o
	}
	return orgRoutings.add(key, h.buildOrgRouting(r, gr, global, deps))
}

func (o *orgRouting) builtFrom(gr *routing.Router, global *routing.Failover, deps *InjectedDeps) bool {
	return o.router == gr && o.global == global && o.deps == deps
}

// orgSelects reports whether r changes how targets are selected: a strategy,
// adaptive routing, a circuit breaker or the org's own failover.
func orgSelects(r *tenant.RoutingConfig) bool {
	return r.Strategy != "" || (r.Adaptive != nil && r.Adaptive.Enabled) ||
		(r.CircuitBreaker != nil && r.CircuitBreaker.Enabled) || orgFailoverOn(r)
}

func orgComplexity(r *tenant.RoutingConfig) bool { return r.Complexity != nil && r.Complexity.Enabled }

// orgFailoverOn reports whether the org turned its own failover on: its
// provider failover, or its model fallback chains (FallbackOn: the switch as
// set, else on when it has chains), which move a request down a chain on its
// failover's codes.
func orgFailoverOn(r *tenant.RoutingConfig) bool {
	return r.FallbackOn() || (r.Failover != nil && r.Failover.Enabled)
}

func (h *Handlers) buildOrgRouting(r *tenant.RoutingConfig, gr *routing.Router, global *routing.Failover, deps *InjectedDeps) *orgRouting {
	o := &orgRouting{router: gr, global: global, deps: deps}
	if orgComplexity(r) {
		o.complexity = routing.NewComplexityAnalyzer(orgComplexityConfig(r.Complexity))
	}
	if !orgSelects(r) {
		return o
	}
	cfg, retryer := h.orgFailoverSettings(r, global)
	if !cfg.Enabled {
		return o // no failover runs, so there is nothing for the org's router to select
	}
	// Without a gateway router the org failover still decides which errors
	// move a request down its model fallback chain (shouldTryModelFallback).
	breakers := orgBreakers(r, global)
	var router *routing.Router
	if gr != nil {
		var s routing.Strategy
		s, o.strategy = h.orgStrategy(r, gr, deps)
		router = routing.NewStrategyRouter(gr, routing.SkipOpenCircuits(s, breakers))
	}
	o.failover = routing.NewFailover(cfg, router, retryer, breakers)
	return o
}

// orgStrategy builds the strategy the org's router selects with, from the
// deps in force: the org's choice, else (or when the org names an unknown
// one) the gateway's, with the gateway's adaptive tuning. It also returns
// the canonical name of the org's choice, "" when the router keeps the
// gateway's strategy.
func (h *Handlers) orgStrategy(r *tenant.RoutingConfig, gr *routing.Router, deps *InjectedDeps) (routing.Strategy, string) {
	sdeps := routing.StrategyDeps{
		Latency:     gr.GetLatencyTracker(),
		Health:      h.healthMonitor,
		ModelHealth: deps.ModelHealth,
		Prices:      deps.Prices,
		Adaptive:    orgAdaptiveConfig(r.Adaptive),
	}
	if name, chosen := orgStrategyName(r); chosen {
		s, err := routing.NewStrategy(name, sdeps)
		if err == nil {
			return s, s.Name()
		}
		slog.Warn("org routing strategy is unknown; the org's router keeps the gateway's", "strategy", name, "error", err)
	}
	s, err := routing.StrategyLike(gr, sdeps)
	if err != nil { // the gateway router reports only names NewStrategy built
		s = routing.NewRoundRobin()
	}
	return s, ""
}

// orgStrategyName is the strategy the org chose, and whether it chose one.
// The adaptive toggle wins over strategy: the console writes both side by
// side, and a strategy of round_robin is its default.
func orgStrategyName(r *tenant.RoutingConfig) (string, bool) {
	if r.Adaptive != nil && r.Adaptive.Enabled {
		return routing.StrategyAdaptive, true
	}
	return r.Strategy, r.Strategy != ""
}

// orgFailoverSettings is the org's failover when it turns its own on (with
// its own retry), else the global failover's.
func (h *Handlers) orgFailoverSettings(r *tenant.RoutingConfig, global *routing.Failover) (config.FailoverConfig, *routing.Retryer) {
	if orgFailoverOn(r) {
		var retryer *routing.Retryer
		if r.Retry != nil && r.Retry.Enabled {
			retryer = routing.NewRetryer(h.buildOrgRetryConfig(r.Retry))
		}
		return h.buildOrgFailoverConfig(r), retryer
	}
	if global != nil {
		return global.Settings()
	}
	return config.FailoverConfig{}, nil
}

// orgBreakers is the org's own circuit breaker registry when it configures
// one, else the global one: an org failover never runs without the gateway's
// breakers. An org registry flips no provider health; its circuits are its
// own.
func orgBreakers(r *tenant.RoutingConfig, global *routing.Failover) *routing.CircuitBreakerRegistry {
	if r.CircuitBreaker != nil && r.CircuitBreaker.Enabled {
		return routing.NewCircuitBreakerRegistry(orgCircuitBreakerConfig(r.CircuitBreaker), nil)
	}
	return global.Breakers()
}

func orgCircuitBreakerConfig(c *tenant.CircuitBreakerConfig) config.CircuitBreakerConfig {
	return config.CircuitBreakerConfig{
		Enabled:          c.Enabled,
		FailureThreshold: c.FailureThreshold,
		SuccessThreshold: c.SuccessThreshold,
		Cooldown:         parseOrgDuration(c.Cooldown),
		OnStatusCodes:    c.OnStatusCodes,
	}
}

func orgAdaptiveConfig(a *tenant.AdaptiveRoutingConfig) config.AdaptiveConfig {
	if a == nil {
		return config.AdaptiveConfig{}
	}
	cfg := config.AdaptiveConfig{
		Enabled:          a.Enabled,
		LearningRequests: a.LearningRequests,
		UpdateInterval:   parseOrgDuration(a.UpdateInterval),
		SmoothingFactor:  a.SmoothingFactor,
		MinWeight:        a.MinWeight,
	}
	if w := a.SignalWeights; w != nil {
		cfg.SignalWeights = config.SignalWeightsConfig{Latency: w.Latency, ErrorRate: w.ErrorRate, Cost: w.Cost}
	}
	return cfg
}

func orgComplexityConfig(c *tenant.ComplexityRoutingConfig) config.ComplexityConfig {
	cfg := config.ComplexityConfig{Enabled: c.Enabled, DefaultTier: c.DefaultTier, Weights: c.Weights,
		Tiers: make(map[string]config.ComplexityTierConfig, len(c.Tiers))}
	for name, t := range c.Tiers {
		if t != nil {
			cfg.Tiers[name] = config.ComplexityTierConfig{MaxScore: t.MaxScore, Model: t.Model, Provider: t.Provider}
		}
	}
	return cfg
}

// parseOrgDuration reads an org config duration; invalid or empty is 0, so
// the consumer's default applies.
func parseOrgDuration(s string) time.Duration {
	if s == "" {
		return 0
	}
	d, err := time.ParseDuration(s)
	if err != nil {
		return 0
	}
	return d
}

func (h *Handlers) buildOrgFailoverConfig(r *tenant.RoutingConfig) config.FailoverConfig {
	cfg := config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
		OnTimeout:   true,
	}

	if r.Failover != nil {
		if r.Failover.MaxAttempts > 0 {
			cfg.MaxAttempts = r.Failover.MaxAttempts
		}
		cfg.OnStatusCodes = r.Failover.OnStatusCodes
		cfg.OnTimeout = r.Failover.OnTimeout
		if r.Failover.PerAttemptTimeout != "" {
			if d, err := time.ParseDuration(r.Failover.PerAttemptTimeout); err == nil {
				cfg.PerAttemptTimeout = d
			}
		}
	}

	// Legacy flat fields as fallback.
	if len(cfg.OnStatusCodes) == 0 && len(r.FallbackStatusCodes) > 0 {
		cfg.OnStatusCodes = r.FallbackStatusCodes
	}
	if len(cfg.OnStatusCodes) == 0 {
		cfg.OnStatusCodes = routing.DefaultFailoverStatusCodes
	}

	return cfg
}

func (h *Handlers) buildOrgRetryConfig(r *tenant.RetryConfig) config.RetryConfig {
	cfg := config.RetryConfig{
		Enabled:    r.Enabled,
		MaxRetries: r.MaxRetries,
		Multiplier: r.Multiplier,
		OnTimeout:  r.OnTimeout,
	}
	if r.InitialDelay != "" {
		if d, err := time.ParseDuration(r.InitialDelay); err == nil {
			cfg.InitialDelay = d
		}
	}
	if r.MaxDelay != "" {
		if d, err := time.ParseDuration(r.MaxDelay); err == nil {
			cfg.MaxDelay = d
		}
	}
	if len(r.OnStatusCodes) > 0 {
		cfg.OnStatusCodes = r.OnStatusCodes
	}
	return cfg
}

// orgRoutingCacheSize bounds the per-org instances kept.
const orgRoutingCacheSize = 1024

// orgRoutings caches per-org instances by the routing config a request runs
// under: the tenant store replaces an org's config on every push, and a
// staged rollout's candidate is its own routing block, so one key is one
// (org, meta.version, variant). A push of any routing field, or an eviction,
// therefore builds a new instance whose own circuits start closed: an org's
// open circuit closes when its config changes. It lives here rather than on
// Handlers, so the Handlers struct keeps its fields;
// the key carries the Handlers, and entries of a Handlers not in use
// stay until evicted (one Handlers runs in a gateway; tests build many).
var orgRoutings = newOrgRoutingCache(orgRoutingCacheSize)

type orgRoutingKey struct {
	h       *Handlers
	routing *tenant.RoutingConfig
}

type orgRoutingEntry struct {
	key orgRoutingKey
	val *orgRouting
}

// orgRoutingCache is an LRU of per-org instances.
type orgRoutingCache struct {
	mu      sync.Mutex
	max     int
	order   *list.List // of *orgRoutingEntry, most recently used first
	entries map[orgRoutingKey]*list.Element
}

func newOrgRoutingCache(max int) *orgRoutingCache {
	return &orgRoutingCache{max: max, order: list.New(), entries: make(map[orgRoutingKey]*list.Element)}
}

func (c *orgRoutingCache) get(key orgRoutingKey) *orgRouting {
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.entries[key]
	if !ok {
		return nil
	}
	c.order.MoveToFront(el)
	return el.Value.(*orgRoutingEntry).val
}

// add stores o under key and returns the instance to use: one a concurrent
// build stored first from the same inputs wins, so all of an org's requests
// share one set of circuits.
func (c *orgRoutingCache) add(key orgRoutingKey, o *orgRouting) *orgRouting {
	c.mu.Lock()
	defer c.mu.Unlock()
	if el, ok := c.entries[key]; ok {
		e := el.Value.(*orgRoutingEntry)
		c.order.MoveToFront(el)
		if e.val.builtFrom(o.router, o.global, o.deps) {
			return e.val
		}
		e.val = o
		return o
	}
	c.entries[key] = c.order.PushFront(&orgRoutingEntry{key: key, val: o})
	for c.order.Len() > c.max {
		last := c.order.Back()
		c.order.Remove(last)
		delete(c.entries, last.Value.(*orgRoutingEntry).key)
	}
	return o
}
