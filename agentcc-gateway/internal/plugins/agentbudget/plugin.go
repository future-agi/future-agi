// Package agentbudget is per-run agent budgets: a run scope's
// hard limit shared by every call in the run (main turns, members, retries
// and fallbacks), a per-call cost cap, and the budget left, for managed and
// BYOK calls alike.
//
// A call with no run scope that sends x-agentcc-max-cost-usd,
// x-agentcc-budget-remaining-usd or x-agentcc-budget-usd is capped on its own
// (unscopedCap): no run, no Redis, nothing held.
package agentbudget

import (
	"context"
	"errors"
	"log/slog"
	"math"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Priority is the run-budget reserve's place among the pre-plugins: after the
// credits reserve (510). Its settle runs after the credits settle, by the same
// number.
const Priority = 515

// Settings are the managed.* config values the run budget's worst-case
// estimate uses (config.ManagedConfig).
type Settings struct {
	MarkupPercent              float64
	HoldTTL                    time.Duration
	ReserveDefaultOutputTokens int
}

func (s Settings) markupBps() int {
	if !(s.MarkupPercent > 0) {
		return 0
	}
	return int(math.Round(s.MarkupPercent * 100))
}

// Deps are what the plugin reads; the functions are read per call, so the
// wiring may hand over closures over values it sets later.
type Deps struct {
	// Store is the scope store; nil without Redis, when a call that sends a
	// budget fails by budgets.fail_mode and one that sends none holds nothing.
	Store    *redisstate.AgentBudgetStore
	Settings Settings
	// Prices is an endpoint's price card (the gateway's injected prices); nil
	// leaves every model unpriced.
	Prices models.PriceLookup
	// MaxOutput is a model's max output tokens, 0 when unknown.
	MaxOutput func(providerID, model string) int
	// OrgConfig is an org's stored config, for calls that reach the plugin
	// without the runBudgetCap hook (embeddings and rerank).
	OrgConfig func(orgID string) *tenant.OrgConfig
}

// Plugin is the run-budget pipeline plugin and the runBudgetCap hook's
// implementation. A call in a run scope (scope.go) is refused when its run
// was stopped or its budget is spent, capped per call before routing
// (cap.go), held on its scope at its worst-case cost at priority 515 and
// settled after the call at what it cost (settle.go). A call with no run scope
// is untouched unless it sends a cap; then it is capped on its own and nothing
// is held.
type Plugin struct {
	deps   Deps
	scopes *Scopes // nil without a store
	states sync.Map
}

// New returns the plugin over deps.
func New(deps Deps) *Plugin {
	p := &Plugin{deps: deps}
	if deps.Store != nil {
		p.scopes = NewScopes(deps.Store, deps.Settings.HoldTTL)
	}
	return p
}

// Scopes is the plugin's scope store, for callers outside the pipeline; nil
// without Redis.
func (p *Plugin) Scopes() *Scopes { return p.scopes }

func (p *Plugin) Name() string  { return "agentbudget" }
func (p *Plugin) Priority() int { return Priority }

// ShouldSkipOnCacheHit is false: a call held before a cache answered it
// must still be voided.
func (p *Plugin) ShouldSkipOnCacheHit() bool { return false }

// runState is one call's run-budget state, from the hook or the reserve to
// the settle or the request's end.
type runState struct {
	org, scope, holdID string
	limitMicros        int64 // the budget the call sends, or AgentBudgetNoLimit
	pol                policy

	capMicros     int64 // the per-call cap, -1 for none
	capFromBudget bool  // the scope's remainder is the cap's binding term

	skipStore bool // nothing to hold: no store and no budget sent, or fail-open
	reserved  bool
	estMicros int64 // the call's worst case, as held
	estPriced bool
	done      atomic.Bool // settled or voided
}

// policy is the org's run-budget settings.
type policy struct {
	scopeTTL         time.Duration
	allowUnpriced    bool  // routing.agents.unpriced_under_cap=allow
	failOpen         bool  // budgets.fail_mode=open
	maxCallMicros    int64 // managed_models.max_call_cost_usd, -1 for none
	defaultMaxTokens int   // managed_models.default_max_tokens_estimate as stored, 0 unset
}

func policyOf(cfg *tenant.OrgConfig) policy {
	a := cfg.AgentsConfig()
	pol := policy{scopeTTL: time.Duration(a.BudgetScopeTTLSeconds) * time.Second,
		allowUnpriced: a.UnpricedUnderCap == "allow", failOpen: cfg.BudgetsFailMode() == "open", maxCallMicros: -1}
	if m := cfg.ManagedPolicy().MaxCallCostUSD; m != nil {
		pol.maxCallMicros = billing.USDToMicros(*m)
	}
	if cfg != nil && cfg.ManagedModels != nil && cfg.ManagedModels.DefaultMaxTokensEstimate != nil {
		pol.defaultMaxTokens = max(*cfg.ManagedModels.DefaultMaxTokensEstimate, 0)
	}
	return pol
}

// begin returns rc's run state, creating it, and the finalizer that voids an
// unsettled hold, on first use; nil for a call with no run scope that sends
// no cap, or one whose spend another path counts. A call with no run scope
// that sends a cap gets a state with scope "" that holds nothing. cfg is the hook's effective org config;
// the reserve's first use reads the org's stored one.
func (p *Plugin) begin(rc *models.RequestContext, cfg *tenant.OrgConfig, fromHook bool) *runState {
	if v, ok := p.states.Load(rc); ok {
		return v.(*runState)
	}
	if !counted(rc) {
		return nil
	}
	scope := scopeOf(rc)
	if scope == "" && !sendsCallCap(rc) {
		return nil
	}
	st := &runState{org: orgIDOf(rc), scope: scope, holdID: holdIDOf(rc), limitMicros: limitMicrosOf(rc), capMicros: -1}
	if scope == "" {
		// A call outside any run: capped on its own, nothing to hold.
		st.limitMicros, st.skipStore = redisstate.AgentBudgetNoLimit, true
	}
	if !fromHook && p.deps.OrgConfig != nil {
		cfg = p.deps.OrgConfig(st.org)
	}
	st.pol = policyOf(cfg)
	p.states.Store(rc, st)
	rc.AddFinalizer(func() { p.exit(rc, st) })
	return st
}

// errNoStore is the failure of a budget sent to a gateway without Redis.
var errNoStore = errors.New("agentbudget: the gateway has no Redis for run budgets")

// unavailable is the answer when the scope store fails: the 503
// budget_state_unavailable, or nil with nothing held when the org sets
// budgets.fail_mode=open.
func (p *Plugin) unavailable(rc *models.RequestContext, st *runState, err error) *models.APIError {
	if st.pol.failOpen {
		st.skipStore = true
		slog.Warn("run budget unavailable; admitting the call unheld (budgets.fail_mode=open)",
			"request_id", rc.RequestID, "org_id", st.org, "scope", st.scope, "error", err)
		return nil
	}
	slog.Error("run budget unavailable; refusing the call (budgets.fail_mode=closed)",
		"request_id", rc.RequestID, "org_id", st.org, "scope", st.scope, "error", err)
	return ErrBudgetStateUnavailable(st.scope)
}

// ProcessRequest fits a call in a run scope under its per-call cap and
// holds its worst case on the scope.
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	st := p.begin(rc, nil, false)
	if st == nil {
		return pipeline.ResultContinue()
	}
	est := p.estimate(rc, st)
	if apiErr := p.fit(rc, st, &est); apiErr != nil {
		return pipeline.ResultError(apiErr)
	}
	if apiErr := p.hold(ctx, rc, st, est); apiErr != nil {
		return pipeline.ResultError(apiErr)
	}
	return pipeline.ResultContinue()
}

// hold takes the call's hold on its scope.
func (p *Plugin) hold(ctx context.Context, rc *models.RequestContext, st *runState, est estimate) *models.APIError {
	if st.skipStore {
		return nil
	}
	if p.scopes == nil {
		if st.limitMicros < 0 {
			st.skipStore = true // no limit can exist without a store
			return nil
		}
		return p.unavailable(rc, st, errNoStore)
	}
	res, refusal, err := p.scopes.Reserve(ctx, st.org, st.scope, st.holdID, est.micros,
		ScopeOptions{LimitMicros: st.limitMicros, TTL: st.pol.scopeTTL})
	if err != nil {
		return p.unavailable(rc, st, err)
	}
	if refusal != nil {
		return refusal
	}
	st.reserved, st.estMicros, st.estPriced = true, est.micros, est.priced
	if res.HasLimit() {
		recordRemaining(rc, res.RemainingMicros())
	}
	return nil
}

// ProcessResponse settles the call's hold at what it cost.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if v, ok := p.states.Load(rc); ok {
		p.finish(rc, v.(*runState))
	}
	return pipeline.ResultContinue()
}

// exit is the request finalizer: it voids a hold nothing settled (a panic, a
// timeout, an exit path without post-plugins) and forgets the call.
func (p *Plugin) exit(rc *models.RequestContext, st *runState) {
	if st.reserved && st.done.CompareAndSwap(false, true) {
		if _, err := p.scopes.Void(context.Background(), st.org, st.scope, st.holdID, st.pol.scopeTTL); err != nil {
			slog.Warn("run budget: void on exit failed; the hold expires on its own",
				"request_id", rc.RequestID, "org_id", st.org, "scope", st.scope, "error", err)
		}
	}
	p.states.Delete(rc)
}
