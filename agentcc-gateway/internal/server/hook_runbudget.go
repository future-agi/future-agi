package server

import (
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// RunBudget is how the chat path reaches per-run agent budgets
// without importing them: the run-budget plugin implements it, and the
// wiring installs it with SetRunBudget.
type RunBudget interface {
	// CallCap refuses a call whose run was stopped or whose budget is spent,
	// and sets rc.Billing.CallCapUSD, before routing. orgCfg is the
	// effective org config.
	CallCap(rc *models.RequestContext, orgCfg *tenant.OrgConfig) *models.APIError
}

// runBudgets holds each server's run budget, keyed by its handlers, so
// servers built side by side (tests) never share one. Nil until the wiring
// installs it: every call passes.
var runBudgets sync.Map // *Handlers -> RunBudget

// SetRunBudget installs the run budget the runBudgetCap hook calls; nil
// removes it. Call it before the server serves.
func (h *Handlers) SetRunBudget(rb RunBudget) {
	if rb == nil {
		runBudgets.Delete(h)
		return
	}
	runBudgets.Store(h, rb)
}

// runBudgetCap is call-order step 10: it refuses a call whose run budget is
// spent or whose run was stopped, and sets rc.Billing.CallCapUSD, before
// routing. Keep observeHook as its first call.
func (h *Handlers) runBudgetCap(rc *models.RequestContext, orgCfg *tenant.OrgConfig) *models.APIError {
	h.observeHook(hookRunBudgetCap, rc)
	rb, ok := runBudgets.Load(h)
	if !ok {
		return nil
	}
	return rb.(RunBudget).CallCap(rc, orgCfg)
}
