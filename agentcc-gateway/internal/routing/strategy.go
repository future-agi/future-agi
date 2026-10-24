package routing

import (
	"fmt"
	"strings"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Strategy selects a target from a list of healthy routing targets.
type Strategy interface {
	// Name returns the strategy identifier.
	Name() string

	// Select picks a target index from the given slice.
	// The slice contains only healthy targets.
	// Returns the index into the slice, or an error if no valid target.
	Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error)
}

// ModelStrategy is a Strategy that ranks targets by what serving one model
// costs or how it performs. The router passes the routed model to
// SelectModel, so a target without a model
// override is ranked for that model. Select has no model: it ranks a target
// by its own override, and one without is unpriced and has only its
// provider's health.
type ModelStrategy interface {
	Strategy
	SelectModel(model string, targets []RoutingTarget, tracker *LatencyTracker) (int, error)
}

// StrategyDeps are what a strategy reads when it selects: provider latency
// EWMAs and request counts, per-model health and prices. They come from the
// gateway's injected dependencies when a router is built after startup; a
// strategy built at config time gets them later through BindStrategyDeps.
type StrategyDeps struct {
	Latency     *LatencyTracker
	Health      *HealthMonitor
	ModelHealth ModelHealthReader
	Prices      models.PriceLookup
	// Adaptive tunes the adaptive strategy.
	Adaptive config.AdaptiveConfig
}

// Strategy names. Org configs write them with underscores (cost_optimized),
// config.yaml with hyphens (cost-optimized); both name the same strategy.
const (
	StrategyRoundRobin    = "round-robin"
	StrategyWeighted      = "weighted"
	StrategyLeastLatency  = "least-latency"
	StrategyCostOptimized = "cost-optimized"
	StrategyAdaptive      = "adaptive"
	StrategyFastest       = "fastest"
)

// CanonicalStrategy returns name in the hyphenated form NewStrategy reports,
// so "cost_optimized" and "Cost-Optimized" are both "cost-optimized". The
// empty name is round-robin.
func CanonicalStrategy(name string) string {
	name = strings.ToLower(strings.ReplaceAll(strings.TrimSpace(name), "_", "-"))
	if name == "" {
		return StrategyRoundRobin
	}
	return name
}

// NewStrategy creates a strategy by name, with the deps it reads when it
// selects. deps is optional so config-time callers (NewRouter) can name a
// strategy before the deps exist; such a strategy gets them through
// BindStrategyDeps, and until then cost-optimized ranks by priority and
// adaptive round-robins. "fastest" is handled at the handler level via
// RaceExecutor, not as a Strategy, so it round-robins here.
func NewStrategy(name string, deps ...StrategyDeps) (Strategy, error) {
	var d *StrategyDeps
	if len(deps) > 0 {
		d = &deps[0]
	}
	switch CanonicalStrategy(name) {
	case StrategyRoundRobin:
		return NewRoundRobin(), nil
	case StrategyWeighted:
		return &WeightedStrategy{}, nil
	case StrategyLeastLatency:
		return &LeastLatencyStrategy{}, nil
	case StrategyCostOptimized:
		s := NewCostOptimized()
		s.deps.fix(d)
		return s, nil
	case StrategyAdaptive:
		var cfg config.AdaptiveConfig
		if d != nil {
			cfg = d.Adaptive
		}
		return NewAdaptiveStrategy(cfg, d), nil
	case StrategyFastest:
		return NewRoundRobin(), nil
	default:
		return nil, fmt.Errorf("unknown routing strategy %q (supported: round-robin, weighted, least-latency, cost-optimized, adaptive, fastest)", name)
	}
}

// StrategyLike returns a new strategy of the kind r selects with, reading
// deps: r's strategy name, and for adaptive the tuning r's strategy was
// built with (config.yaml's routing.adaptive) rather than deps.Adaptive. A
// failover that keeps
// the gateway's strategy selects with it.
func StrategyLike(r *Router, deps StrategyDeps) (Strategy, error) {
	if a, ok := r.defaultStrategy.(*AdaptiveStrategy); ok {
		return a.withDeps(deps), nil
	}
	return NewStrategy(r.Strategy(), deps)
}

// strategyDeps is where a strategy reads its deps: fixed at construction,
// or bound later from a source read at every selection.
type strategyDeps struct {
	fixed *StrategyDeps
	late  atomic.Pointer[func() StrategyDeps]
}

func (s *strategyDeps) fix(d *StrategyDeps) {
	if d != nil {
		cp := *d
		s.fixed = &cp
	}
}

// get returns the deps in force now; the zero value when there are none.
func (s *strategyDeps) get() StrategyDeps {
	if s.fixed != nil {
		return *s.fixed
	}
	if src := s.late.Load(); src != nil {
		return (*src)()
	}
	return StrategyDeps{}
}

// awaits reports whether the strategy has no deps yet.
func (s *strategyDeps) awaits() bool { return s.fixed == nil && s.late.Load() == nil }

// depsBinder is a strategy that reads deps.
type depsBinder interface{ strategyDepsCell() *strategyDeps }

// StrategyAwaitsDeps reports whether r's strategy reads deps it has not been
// given: one NewRouter built from config before the gateway's deps existed.
func StrategyAwaitsDeps(r *Router) bool {
	if r == nil {
		return false
	}
	b, ok := r.defaultStrategy.(depsBinder)
	return ok && b.strategyDepsCell().awaits()
}

// BindStrategyDeps gives r's strategy a source of deps, read at every
// selection, when it awaits them; it reports whether it bound src. A router's
// strategy is set when the router is built and never replaced, so this reads
// it without the router's lock.
func BindStrategyDeps(r *Router, src func() StrategyDeps) bool {
	if !StrategyAwaitsDeps(r) || src == nil {
		return false
	}
	return r.defaultStrategy.(depsBinder).strategyDepsCell().late.CompareAndSwap(nil, &src)
}

// NewStrategyRouter returns a router over base's targets that selects with
// s: an org's own strategy over the gateway's targets. base's latency
// tracker is shared. Its targets are copied as configured, all healthy: a
// provider-wide health flag of the moment would stay in the copy until it
// was rebuilt, and the failover over this router excludes failing targets
// through its circuits. A router reload builds a new base.
//
// A target keeps the model override it was configured with and gets none
// otherwise. The failover's call sends a target's override in place of the
// model the request carries, so naming the routed model on every target
// would undo a step before failover that rewrote the request's model (a
// conditional route's model_override). s learns the routed model from the
// router (ModelStrategy.SelectModel), which is
// how it prices a target without an override and reads its circuit.
func NewStrategyRouter(base *Router, s Strategy) *Router {
	r := &Router{
		modelTargets:    make(map[string][]RoutingTarget),
		defaultStrategy: s,
		latencyTracker:  base.latencyTracker,
	}
	base.mu.RLock()
	defer base.mu.RUnlock()
	for model, targets := range base.modelTargets {
		copied := make([]RoutingTarget, len(targets))
		for i, t := range targets {
			t.Healthy = true
			copied[i] = t
		}
		r.modelTargets[model] = copied
	}
	return r
}
