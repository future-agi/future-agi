package server

import (
	"container/list"
	"context"
	"log/slog"
	"math"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Customer keys over the org's own providers. A key limited to its
// org's providers (orgProvidersOnly: every key but an internal one; a
// Django-minted key syncs as byok) never runs the gateway's failover, which
// selects among config.yaml providers such a key must never reach
// (orgRouterSelects, useProviderFailover). customerFailover
// gives it the org's own: a failover over a router whose targets are the
// org's provider instances only, with the org's retry, its own circuit
// breakers and its strategy, whenever the org configured any of them.
//
// Target ids are org-scoped: "org:<org_id>/<provider_id>". The org's circuit
// breakers are therefore keyed (routing.BreakerKey) "org:<org_id>/<provider_id>|<model>"
// in a registry of the org's own, so no operator circuit and no other org's
// can refuse an org's provider, nor an org's open circuit the operator's.
// A target id is mapped back to the org's provider id through the table
// built with it, never parsed, and a call only ever builds the org's own
// instance of that provider with the org's own credentials.

// orgTargetPrefix starts every org-scoped target id.
const orgTargetPrefix = "org:"

// orgTargetID is the org-scoped target id of an org's provider.
func orgTargetID(orgID, providerID string) string {
	return orgTargetPrefix + orgID + "/" + providerID
}

// orgTargetWeight is the weighted strategy's integer weight for an org
// provider's weight: a relative share to two decimals, unset (0) or negative
// counting as 1 (the console's default).
func orgTargetWeight(w float64) int {
	if w <= 0 {
		w = 1
	}
	if n := int(math.Round(w * 100)); n > 0 {
		return n
	}
	return 1
}

// orgFailover is what an org's routing config builds for its customer keys.
type orgFailover struct {
	// What it was built from: the org, its config and the injected deps.
	orgID string
	cfg   *tenant.OrgConfig
	deps  *InjectedDeps

	failover *routing.Failover
	router   *routing.Router
	// latency and health are what the org's strategies read: its own
	// providers' latencies and outcomes, keyed by target id, never the
	// gateway's.
	latency *routing.LatencyTracker
	health  *routing.HealthMonitor
	// ids maps each org-scoped target id to the org's provider id.
	ids map[string]string
	// strategy is the canonical name of the strategy the org chose, "" when
	// it chose none (the providers are then tried in sorted id order).
	strategy string
}

// customerFailover returns the failover a customer key's chat request runs
// over its org's own providers, and the model its router knows the request
// by; nil when it runs none: the key may use only its org's providers, the
// request was routed to one of them (org_provider_model_match), the org
// configured provider failover, retry, a circuit breaker or a strategy, and
// its providers serve the model. A request whose org configured none of
// these allocates nothing here; its model fallback chains run as configured
// (runNonStream, the stream's tryModelFallbacks).
func (h *Handlers) customerFailover(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig) (*orgFailover, string) {
	if orgCfg == nil || orgCfg.Routing == nil || !customerRoutingOn(orgCfg.Routing) || h.orgProviderCache == nil {
		return nil, ""
	}
	key := rc.Metadata["org_provider_model_match"]
	orgID := rc.Metadata["org_id"]
	if key == "" || orgID == "" || !h.orgProvidersOnly(ctx, rc) {
		return nil, ""
	}
	of := h.orgFailoverFor(orgID, orgCfg)
	if of.router.TargetCount(key) == 0 {
		return nil, ""
	}
	return of, key
}

// customerRoutingOn reports whether r configures anything a customer key's
// failover runs: a strategy (or adaptive), provider failover, retry or a
// circuit breaker.
func customerRoutingOn(r *tenant.RoutingConfig) bool {
	if _, chosen := orgStrategyName(r); chosen {
		return true
	}
	return orgProviderFailoverOn(r) || (r.Retry != nil && r.Retry.Enabled) ||
		(r.CircuitBreaker != nil && r.CircuitBreaker.Enabled)
}

// orgProviderFailoverOn reports whether the org turned provider failover on
// (the console's Provider Failover switch): only then does a customer key
// move from one of the org's providers to another. The model fallback switch
// alone moves it down its chain, not across providers.
func orgProviderFailoverOn(r *tenant.RoutingConfig) bool {
	return r.Failover != nil && r.Failover.Enabled
}

// orgFailoverFor returns the org's failover for its config as it stands,
// built once per (org, config) and kept while the deps it read are in force.
// The hit path takes the cache lock and allocates nothing.
func (h *Handlers) orgFailoverFor(orgID string, orgCfg *tenant.OrgConfig) *orgFailover {
	deps := h.Deps()
	key := orgFailoverKey{h: h, orgID: orgID, cfg: orgCfg}
	if of := orgFailovers.get(key); of != nil && of.deps == deps {
		return of
	}
	return orgFailovers.add(key, h.buildOrgFailover(orgID, orgCfg, deps))
}

// buildOrgFailover builds the org's failover: one router target per usable
// org provider and model it lists (in sorted provider-id order, with its
// weight), a provider-qualified name ("<provider>/<model>") pinned to that
// provider, and a model_map entry naming an org provider pinned to it
// (orgMappedRoute's model as the target's model). Its settings: the org's
// failover settings when it turned provider failover on, else one target
// per request; the org's retry when enabled; the org's own circuit breakers
// when enabled; the org's strategy, else the first target that is not
// refused.
func (h *Handlers) buildOrgFailover(orgID string, orgCfg *tenant.OrgConfig, deps *InjectedDeps) *orgFailover {
	r := orgCfg.Routing
	of := &orgFailover{orgID: orgID, cfg: orgCfg, deps: deps, ids: make(map[string]string), latency: routing.NewLatencyTracker(0.3)}
	of.health = routing.NewHealthMonitor(of.latency, nil)

	ids := make([]string, 0, len(orgCfg.Providers))
	for id, pcfg := range orgCfg.Providers {
		if orgProviderUsable(pcfg) {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	targets := make(map[string][]routing.RoutingTarget)
	for _, id := range ids {
		pcfg := orgCfg.Providers[id]
		t := routing.RoutingTarget{ProviderID: orgTargetID(orgID, id), Weight: orgTargetWeight(pcfg.Weight), Healthy: true}
		of.ids[t.ProviderID] = id
		for _, m := range pcfg.Models {
			targets[m] = append(targets[m], t)
			targets[id+"/"+m] = []routing.RoutingTarget{t}
		}
	}
	for name := range orgCfg.ModelMap {
		if providerID, upstream, ok := orgMappedRoute(orgCfg, name); ok {
			t := routing.RoutingTarget{ProviderID: orgTargetID(orgID, providerID), Weight: 1, Healthy: true}
			if upstream != name {
				t.ModelOverride = upstream
			}
			targets[name] = []routing.RoutingTarget{t}
		}
	}

	var breakers *routing.CircuitBreakerRegistry
	if r.CircuitBreaker != nil && r.CircuitBreaker.Enabled {
		breakers = routing.NewCircuitBreakerRegistry(orgCircuitBreakerConfig(r.CircuitBreaker), nil)
	}
	of.router = routing.NewTargetRouter(targets, routing.SkipOpenCircuits(of.orgStrategy(r, deps), breakers), of.latency)

	cfg := config.FailoverConfig{Enabled: true, MaxAttempts: 1}
	if orgProviderFailoverOn(r) {
		cfg = h.buildOrgFailoverConfig(r)
	}
	var retryer *routing.Retryer
	if r.Retry != nil && r.Retry.Enabled {
		retryer = routing.NewRetryer(h.buildOrgRetryConfig(r.Retry))
	}
	of.failover = routing.NewFailover(cfg, of.router, retryer, breakers)
	return of
}

// orgStrategy is the strategy the org's router selects with: the org's
// choice, reading the org's own latencies and outcomes (of.latency,
// of.health) and the gateway's prices by the org's provider ids; else (or
// for an unknown name) the first target in order. It reads no operator
// provider health: the org's providers call their upstreams with the org's
// credentials.
func (of *orgFailover) orgStrategy(r *tenant.RoutingConfig, deps *InjectedDeps) routing.Strategy {
	name, chosen := orgStrategyName(r)
	if !chosen {
		return firstTarget{}
	}
	sdeps := routing.StrategyDeps{
		Latency:     of.latency,
		Health:      of.health,
		ModelHealth: routing.NoModelHealth,
		Adaptive:    orgAdaptiveConfig(r.Adaptive),
	}
	if prices := deps.Prices; prices != nil {
		sdeps.Prices = func(targetID, model, endpointID string) (models.PriceCard, bool) {
			if id, ok := of.ids[targetID]; ok {
				targetID = id
			}
			return prices(targetID, model, endpointID)
		}
	}
	s, err := routing.NewStrategy(name, sdeps)
	if err != nil {
		slog.Warn("org routing strategy is unknown; customer keys try the org's providers in order", "strategy", name, "error", err)
		return firstTarget{}
	}
	of.strategy = s.Name()
	return s
}

// firstTarget selects the first target in configured order: an org failover
// without a strategy tries the org's providers in sorted id order (the
// router has already left out the targets whose circuit refuses).
type firstTarget struct{}

func (firstTarget) Name() string { return "" }

func (firstTarget) Select([]routing.RoutingTarget, *routing.LatencyTracker) (int, error) {
	return 0, nil
}

// target returns, for a failover target id, the org's provider id and the
// org's own instance of it. An id the org's table does not hold, or a
// provider that cannot be built, answers an error: never a gateway provider.
func (of *orgFailover) target(h *Handlers, targetID, model string) (providers.Provider, string, error) {
	id, ok := of.ids[targetID]
	if !ok {
		return nil, "", models.ErrUpstreamProvider(http.StatusBadGateway, "provider "+targetID+" not found")
	}
	pcfg := of.cfg.Providers[id]
	p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(of.orgID, id, pcfg.APIKey, pcfg)
	if err != nil {
		slog.Warn("failed to create org provider for failover", "org_id", of.orgID, "provider", id, "model", model, "error", err)
		return nil, id, orgProviderError(model, id, err)
	}
	return p, id, nil
}

// record records an attempt on targetID that started at started and ended
// with err, for the org's least-latency and adaptive strategies: a served
// attempt's latency and success, or a failure.
func (of *orgFailover) record(targetID string, started time.Time, err error) {
	if err != nil {
		of.health.RecordError(targetID, err)
		return
	}
	of.latency.Record(targetID, time.Since(started))
	of.health.RecordSuccess(targetID)
}

// stampStrategy records the org strategy that chose the target, as
// applyOrgRoutingOverrides does for the org router of an internal key.
func (of *orgFailover) stampStrategy(rc *models.RequestContext) {
	if of.strategy == "" {
		return
	}
	rc.Metadata["routing_strategy"] = of.strategy
	rc.Metadata["routing_strategy_applied"] = of.strategy
	rc.Metadata[routing.RouteReasonStrategy] = of.strategy
}

// chatFailover returns the failover a chat request runs, nil for none: a
// customer key's over its org's own providers (customerFailover), else, for
// a key that may use the gateway's providers, the effective failover over
// them (useProviderFailover). of is the org failover when it is the
// customer key's, and key the model the failover's router knows the request
// by.
func (h *Handlers) chatFailover(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, effective *routing.Failover) (fo *routing.Failover, of *orgFailover, key string) {
	if of, key := h.customerFailover(ctx, rc, orgCfg); of != nil {
		return of.failover, of, key
	}
	if h.useProviderFailover(ctx, effective, rc) {
		return effective, nil, rc.Model
	}
	return nil, nil, ""
}

// routedProvider is the provider the route judged for rc (rc.Provider) when
// of runs the request, "" for the gateway's failover: an org failover whose
// strategy selects another provider admits that attempt as a hop
// (failoverHops.next), so the key's provider ACL and the route's policy hold
// for it too.
func (of *orgFailover) routedProvider(rc *models.RequestContext) string {
	if of == nil {
		return ""
	}
	return rc.Provider
}

// recorded is the provider id a failover result's target id is recorded
// under: the org's provider id for an org failover, else the id itself.
func (of *orgFailover) recorded(targetID string) string {
	if of == nil {
		return targetID
	}
	return of.ids[targetID]
}

// orgFailoverCacheSize bounds the per-org failovers kept.
const orgFailoverCacheSize = 1024

// orgFailovers caches each org's failover by the config it was built from:
// the tenant store replaces an org's config on every push (and a rollout
// candidate is a config of its own), so a push builds a new failover whose
// circuits start closed. The key carries the org id, so two orgs never share
// a failover even when they share one config value.
var orgFailovers = newOrgFailoverCache(orgFailoverCacheSize)

type orgFailoverKey struct {
	h     *Handlers
	orgID string
	cfg   *tenant.OrgConfig
}

type orgFailoverEntry struct {
	key orgFailoverKey
	val *orgFailover
}

// orgFailoverCache is an LRU of per-org failovers.
type orgFailoverCache struct {
	mu      sync.Mutex
	max     int
	order   *list.List // of *orgFailoverEntry, most recently used first
	entries map[orgFailoverKey]*list.Element
}

func newOrgFailoverCache(max int) *orgFailoverCache {
	return &orgFailoverCache{max: max, order: list.New(), entries: make(map[orgFailoverKey]*list.Element)}
}

func (c *orgFailoverCache) get(key orgFailoverKey) *orgFailover {
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.entries[key]
	if !ok {
		return nil
	}
	c.order.MoveToFront(el)
	return el.Value.(*orgFailoverEntry).val
}

// add stores of under key and returns the failover to use: one a concurrent
// build stored first from the same deps wins, so all of an org's requests
// share one set of circuits.
func (c *orgFailoverCache) add(key orgFailoverKey, of *orgFailover) *orgFailover {
	c.mu.Lock()
	defer c.mu.Unlock()
	if el, ok := c.entries[key]; ok {
		e := el.Value.(*orgFailoverEntry)
		c.order.MoveToFront(el)
		if e.val.deps == of.deps {
			return e.val
		}
		e.val = of
		return of
	}
	c.entries[key] = c.order.PushFront(&orgFailoverEntry{key: key, val: of})
	for c.order.Len() > c.max {
		last := c.order.Back()
		c.order.Remove(last)
		delete(c.entries, last.Value.(*orgFailoverEntry).key)
	}
	return of
}
