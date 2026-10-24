package agentbudget

import (
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Error codes of the run-budget refusals (error-402-v1), and the 503 a run
// budget fails closed with.
const (
	CodeBudgetExhausted        = "agent_budget_exhausted"
	CodeCallCapExceeded        = "agent_call_cap_exceeded"
	CodeCallCapUnenforceable   = "call_cap_unenforceable"
	CodeRunStopped             = "agent_run_stopped"
	CodeBudgetStateUnavailable = "budget_state_unavailable"
)

// Remedy hints of the refusals.
const (
	remedyRaiseBudget = "raise_run_budget"
	remedyLowerTokens = "lower_max_tokens"
	remedyPricedModel = "use_priced_model"
	remedyStartNewRun = "start_new_run"
	remedyRetry       = "retry"
)

// Every run-budget limit refusal names the run scope, run:<scope>, and lifts
// only on a new run or a higher budget: resets_at is null. A call outside any
// run (scope "") has no scope to name: metadata.scope is left out.
func runMeta(source, remedy, scope string) models.PaymentMeta {
	meta := models.PaymentMeta{LimitSource: source, RemedyHint: remedy}
	if scope != "" {
		meta.Scope = models.ScopeRun(scope)
	}
	return meta
}

func usdPtr(micros int64) *float64 {
	v := billing.MicrosToUSD(micros)
	return &v
}

// ErrBudgetExhausted is the 402 for a call the run's budget cannot hold:
// requiredMicros is the call's worst case (negative when it is not known
// yet, and then not sent), availableMicros what the scope can still hold
// (limit - spent - held, floored at 0).
func ErrBudgetExhausted(scope string, requiredMicros, availableMicros int64) *models.APIError {
	meta := runMeta(models.LimitSourceAgentBudget, remedyRaiseBudget, scope)
	meta.AvailableUSD = usdPtr(max(availableMicros, 0))
	msg := fmt.Sprintf("The run budget for %s is spent. Raise the run's budget or start a new run.", scope)
	if requiredMicros >= 0 {
		meta.RequiredUSD = usdPtr(requiredMicros)
		msg = fmt.Sprintf("The run budget for %s has $%.6f left, which does not cover this call's worst-case cost ($%.6f). "+
			"Raise the run's budget or start a new run.", scope, *meta.AvailableUSD, *meta.RequiredUSD)
		if scope == "" {
			msg = fmt.Sprintf("This call's budget (x-agentcc-budget-usd, $%.6f; it sent no run id, so the budget covers this call alone) "+
				"does not cover its worst-case cost ($%.6f). Raise the budget, lower max_tokens or choose a cheaper model.",
				*meta.AvailableUSD, *meta.RequiredUSD)
		}
	}
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, CodeBudgetExhausted, msg, meta)
}

// ErrCallCapExceeded is the 402 for a call whose worst case is over its
// per-call cap (max-cost-usd, budget-remaining-usd or the org's
// managed_models.max_call_cost_usd) even with max_tokens clamped to 256.
func ErrCallCapExceeded(scope string, requiredMicros, capMicros int64) *models.APIError {
	meta := runMeta(models.LimitSourceCallCap, remedyLowerTokens, scope)
	meta.RequiredUSD, meta.AvailableUSD = usdPtr(max(requiredMicros, 0)), usdPtr(max(capMicros, 0))
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, CodeCallCapExceeded,
		fmt.Sprintf("This call's worst-case cost ($%.6f) is over its per-call cap ($%.6f). "+
			"Lower max_tokens, choose a cheaper model or raise the cap.", *meta.RequiredUSD, *meta.AvailableUSD), meta)
}

// ErrCallCapUnenforceable is the 402 for a call to a model with no price
// under a cap: its worst case cannot be known
// (routing.agents.unpriced_under_cap=allow admits it).
func ErrCallCapUnenforceable(scope, model string) *models.APIError {
	limit := "this run's budget or per-call cap"
	if scope == "" {
		limit = "this call's cost cap"
	}
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, CodeCallCapUnenforceable,
		fmt.Sprintf("Model %q has no price, so its cost cannot be held under %s. "+
			"Choose a priced model.", model, limit), runMeta(models.LimitSourceCallCap, remedyPricedModel, scope))
}

// ErrRunStopped is the 402 for a call in a run that was stopped; reason is the
// stop marker's value (the tool-call guard writes tool_call_blocked:<tool>).
func ErrRunStopped(scope, reason string) *models.APIError {
	meta := runMeta(models.LimitSourceRunStop, remedyStartNewRun, scope)
	meta.Reason = reason
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, CodeRunStopped,
		"This run was stopped ("+reason+"). Start a new run.", meta)
}

// ErrBudgetStateUnavailable is the 503 a call in a run scope gets when the
// scope's budget cannot be read or held and budgets.fail_mode is closed (the
// default): a run budget fails closed.
func ErrBudgetStateUnavailable(scope string) *models.APIError {
	return models.ErrLimit(http.StatusServiceUnavailable, models.ErrTypeServer, CodeBudgetStateUnavailable,
		"The run budget for "+scope+" cannot be checked right now. Retry shortly.",
		models.PaymentMeta{RemedyHint: remedyRetry, Scope: models.ScopeRun(scope), Reason: models.ReasonRedisUnavailable})
}
