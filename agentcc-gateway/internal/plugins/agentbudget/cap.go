package agentbudget

import (
	"context"
	"math"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Response headers the run budget sets.
const (
	HeaderBudgetRemaining  = "x-agentcc-budget-remaining-usd"
	HeaderMaxTokensClamped = "x-agentcc-max-tokens-clamped"
)

// minClampTokens is the smallest max_tokens a call over its cap is clamped
// to; below it the call is refused.
const minClampTokens = 256

// CallCap is the runBudgetCap hook (before routing): a
// call in a stopped run is refused with agent_run_stopped, one whose run has
// spent its limit with agent_budget_exhausted. Otherwise it sets
// rc.Billing.CallCapUSD to the lowest of max-cost-usd, budget-remaining-usd,
// the scope's limit - spent - held and the org's
// managed_models.max_call_cost_usd, each when present, for auto routing to
// filter on and the reserve to fit the call under.
func (p *Plugin) CallCap(rc *models.RequestContext, orgCfg *tenant.OrgConfig) *models.APIError {
	st := p.begin(rc, orgCfg, true)
	if st == nil {
		return nil
	}
	if st.scope == "" {
		setCap(rc, st, unscopedCap(rc))
		return nil
	}
	stored, apiErr := p.peek(rc, st)
	if apiErr != nil {
		return apiErr
	}
	if stored.Stopped {
		return ErrRunStopped(st.scope, stored.StopReason)
	}
	remaining := int64(-1)
	if limit := minMicros(stored.LimitMicros, st.limitMicros); limit >= 0 {
		left := limit - stored.SpentMicros
		if left <= 0 {
			return ErrBudgetExhausted(st.scope, -1, left)
		}
		remaining = max(left-stored.HeldMicros, 0)
		recordRemaining(rc, remaining)
	}
	setCap(rc, st, remaining)
	return nil
}

// unscopedCap is the budget term of a call outside any run, -1 for none: a
// call with no run id is a run of its own, so its budget-usd covers this one
// call, and is not shared with any other. The per-call terms (max-cost-usd,
// budget-remaining-usd and the
// org's managed_models.max_call_cost_usd) apply as in a run (setCap).
func unscopedCap(rc *models.RequestContext) int64 {
	return usdMicros(rc.Agent.BudgetUSD)
}

// peek reads the call's scope. Without a store there is nothing to read: a
// call that sends no budget has nothing a store could enforce.
func (p *Plugin) peek(rc *models.RequestContext, st *runState) (redisstate.AgentBudgetState, *models.APIError) {
	none := redisstate.AgentBudgetState{LimitMicros: redisstate.AgentBudgetNoLimit}
	if p.scopes == nil {
		if st.limitMicros < 0 {
			st.skipStore = true
			return none, nil
		}
		return none, p.unavailable(rc, st, errNoStore)
	}
	stored, err := p.scopes.Peek(context.Background(), st.org, st.scope)
	if err != nil {
		return none, p.unavailable(rc, st, err)
	}
	return stored, nil
}

// setCap sets the call's cap from remaining (the scope's room, -1 when it
// has no limit) and its per-call terms.
func setCap(rc *models.RequestContext, st *runState, remaining int64) {
	perCall := minMicros(minMicros(usdMicros(rc.Agent.MaxCostUSD), usdMicros(rc.Agent.BudgetRemainingUSD)), st.pol.maxCallMicros)
	capMicros := minMicros(perCall, remaining)
	if capMicros < 0 {
		return
	}
	st.capMicros = capMicros
	st.capFromBudget = remaining >= 0 && (perCall < 0 || remaining <= perCall)
	usd := billing.MicrosToUSD(capMicros)
	rc.Billing.CallCapUSD = &usd
}

func usdMicros(usd *float64) int64 {
	if usd == nil {
		return -1
	}
	return billing.USDToMicros(*usd)
}

// recordRemaining puts what the scope can still hold on rc and in the
// x-agentcc-budget-remaining-usd header, floored at 0.
func recordRemaining(rc *models.RequestContext, micros int64) {
	usd := billing.MicrosToUSD(max(micros, 0))
	rc.Billing.RunBudgetRemainingUSD = &usd
	rc.SetResponseHeader(HeaderBudgetRemaining, strconv.FormatFloat(usd, 'f', 6, 64))
}

// estimate is a call's worst case and the card it was priced on.
type estimate struct {
	micros    int64
	card      models.PriceCard
	priced    bool
	markupBps int
	maxOut    int
}

// estimate is the call's worst-case cost on the route it was given, by the
// billing estimator: billed (with the markup) for a managed call, upstream for
// BYOK. A call that is not a chat completion is held at 0 and settled at what
// it cost.
func (p *Plugin) estimate(rc *models.RequestContext, st *runState) estimate {
	req := rc.Request
	if req == nil || (rc.EndpointType != "" && rc.EndpointType != "chat") {
		return estimate{priced: true}
	}
	e := estimate{maxOut: p.defaultMaxOut(rc, st)}
	if isManaged(rc) {
		e.markupBps = p.deps.Settings.markupBps()
	}
	if set := rc.Billing.EndpointSet; len(set) > 0 {
		e.card, e.micros, e.priced = billing.HighestCard(set, req, e.markupBps, e.maxOut)
		return e
	}
	if p.deps.Prices != nil {
		e.card, e.priced = p.deps.Prices(rc.Provider, modelOf(rc), rc.Billing.EndpointID)
	}
	if e.priced {
		e.micros = billing.EstimateWorstCaseMicros(req, e.card, e.markupBps, e.maxOut)
	}
	return e
}

// defaultMaxOut is the output a call with no max_tokens is held for, as the
// credits plugin holds it.
func (p *Plugin) defaultMaxOut(rc *models.RequestContext, st *runState) int {
	configured := p.deps.Settings.ReserveDefaultOutputTokens
	if st.pol.defaultMaxTokens > 0 {
		configured = st.pol.defaultMaxTokens
	}
	modelMax := 0
	if p.deps.MaxOutput != nil {
		modelMax = p.deps.MaxOutput(rc.Provider, modelOf(rc))
	}
	return billing.DefaultMaxOut(modelMax, configured)
}

// fit admits a call under its cap: a call within it as it is, one over it
// with max_tokens clamped to fit when that leaves at least 256 tokens, and
// otherwise refuses it, naming the limit that binds: the run's budget
// (agent_budget_exhausted) or the per-call cap (agent_call_cap_exceeded). An
// unpriced model under a cap is refused with call_cap_unenforceable unless
// the org allows it.
func (p *Plugin) fit(rc *models.RequestContext, st *runState, e *estimate) *models.APIError {
	if st.capMicros < 0 {
		return nil
	}
	if !e.priced {
		if st.pol.allowUnpriced {
			return nil
		}
		return ErrCallCapUnenforceable(st.scope, modelOf(rc))
	}
	if e.micros <= st.capMicros {
		return nil
	}
	if out, micros, ok := clampOutput(rc.Request, e, st.capMicros); ok {
		setMaxTokens(rc.Request, out)
		rc.SetResponseHeader(HeaderMaxTokensClamped, strconv.Itoa(out))
		e.micros = micros
		return nil
	}
	if st.capFromBudget {
		return ErrBudgetExhausted(st.scope, e.micros, st.capMicros)
	}
	return ErrCallCapExceeded(st.scope, e.micros, st.capMicros)
}

// clampOutput is the largest max_tokens, at least 256 and below the call's
// own output cap, whose worst case fits capMicros, with that worst case. The
// first guess solves the estimator's formula; the estimator itself decides.
func clampOutput(req *models.ChatCompletionRequest, e *estimate, capMicros int64) (out int, micros int64, ok bool) {
	if req == nil || !(e.card.Completion > 0) {
		return 0, 0, false
	}
	current := billing.OutputCap(req, e.maxOut)
	room := float64(capMicros)/1e6*10000/float64(10000+max(e.markupBps, 0)) -
		float64(billing.EstimateInputTokens(req))*e.card.Prompt - e.card.Request
	guess := current - 1
	if g := math.Floor(room / e.card.Completion); g < float64(guess) {
		guess = int(g)
	}
	for out = min(guess+1, current-1); out >= guess-2 && out >= minClampTokens; out-- {
		probe := *req
		probe.MaxTokens, probe.MaxCompletionTokens = &out, nil
		if micros = billing.EstimateWorstCaseMicros(&probe, e.card, e.markupBps, e.maxOut); micros <= capMicros {
			return out, micros, true
		}
	}
	return 0, 0, false
}

// setMaxTokens sets the call's output cap to n: max_completion_tokens when
// the call sent only that, else max_tokens (and both when it sent both).
func setMaxTokens(req *models.ChatCompletionRequest, n int) {
	v := n
	if req.MaxCompletionTokens != nil {
		req.MaxCompletionTokens = &v
		if req.MaxTokens == nil {
			return
		}
	}
	req.MaxTokens = &v
}
