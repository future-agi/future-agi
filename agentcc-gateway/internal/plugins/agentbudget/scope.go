package agentbudget

import (
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// noOrg keys the run scopes of a gateway that serves calls with no org (no
// auth, or keys without org_id).
const noOrg = "_"

// scopeOf is the run-budget scope of a call: a runtime token's budget_scope
// (by default its root_run_id), which a header can never move; else
// budget-scope (alias ledger-id); else run-id. "" means the call has no run
// scope, and the run budget does nothing for it.
func scopeOf(rc *models.RequestContext) string {
	if rt := rc.Runtime; rt != nil {
		if rt.BudgetScope != "" {
			return rt.BudgetScope
		}
		if rt.RootRunID != "" {
			return rt.RootRunID
		}
	}
	if rc.Agent.BudgetScope != "" {
		return rc.Agent.BudgetScope
	}
	return rc.Agent.RunID
}

// sendsCallCap reports whether a call sent a cost limit of its own:
// max-cost-usd, budget-remaining-usd or budget-usd (header, x-omega alias or
// metadata.agent). A call with no run scope that sends one is capped on its
// own (unscopedCap); one that sends none is untouched, at no cost.
func sendsCallCap(rc *models.RequestContext) bool {
	a := &rc.Agent
	return a.MaxCostUSD != nil || a.BudgetRemainingUSD != nil || a.BudgetUSD != nil
}

// limitMicrosOf is the budget a call sends for its scope: the lower of a
// runtime token's budget_usd and budget-usd, so a header can only lower a
// claim; redisstate.AgentBudgetNoLimit when it sends none.
func limitMicrosOf(rc *models.RequestContext) int64 {
	limit := rc.Agent.BudgetUSD
	if rt := rc.Runtime; rt != nil && rt.BudgetUSD != nil && (limit == nil || *rt.BudgetUSD < *limit) {
		limit = rt.BudgetUSD
	}
	if limit == nil {
		return redisstate.AgentBudgetNoLimit
	}
	return billing.USDToMicros(*limit)
}

// holdIDOf keys a call's hold: its attempt id, so a repeated attempt reuses
// its hold, else its request id.
func holdIDOf(rc *models.RequestContext) string {
	if rc.Agent.AttemptID != "" {
		return rc.Agent.AttemptID
	}
	return rc.RequestID
}

// orgIDOf is the org a call's scope belongs to.
func orgIDOf(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	if id := rc.Metadata["key_org_id"]; id != "" {
		return id
	}
	return noOrg
}

// counted reports whether the call's spend counts in its run scope here:
// chat, embedding and rerank calls. A capability call is held on its scope
// through Scopes by the capability core's Budget, so counting it here too would
// count it twice.
func counted(rc *models.RequestContext) bool {
	switch rc.EndpointType {
	case "", "chat", "embedding", "rerank":
		return true
	}
	return false
}

// isManaged reports whether the call is billed to its org's managed credit.
func isManaged(rc *models.RequestContext) bool {
	return rc.Metadata["billing_mode"] == models.BillingModeManaged
}

// modelOf is the model the call is sent upstream as.
func modelOf(rc *models.RequestContext) string {
	if rc.Request != nil && rc.Request.Model != "" {
		return rc.Request.Model
	}
	return rc.Model
}

// minMicros is the lower of a and b, where a negative value is "none".
func minMicros(a, b int64) int64 {
	switch {
	case a < 0:
		return b
	case b < 0 || a < b:
		return a
	}
	return b
}
