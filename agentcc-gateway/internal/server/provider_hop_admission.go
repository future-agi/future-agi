package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// admitProviderHop is admitHop for a provider-failover target whose instance is
// already resolved. Carry that instance into eligibility, just as model
// fallbacks do: an unused same-ID org config must not change the wire format.
// Keep admitHop's guards and reservation order intact. The separate entry point
// leaves that shared helper unchanged; callers without a resolved instance
// retain its original behavior.
func (h *Handlers) admitProviderHop(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string, selected ...providers.Provider) string {
	if len(selected) == 0 {
		return h.admitHop(rc, orgCfg, providerID, model)
	}
	if reason := h.mixedBillingRefusal(rc, credentialType(rc)); reason != "" {
		return reason
	}
	if !h.hasAttemptRoom(rc, models.AttemptFailover, providerID, model) {
		return models.ReasonAttemptBudget
	}
	if capbudget.RequestAdmission(rc) != nil {
		return models.ReasonAttemptBudget
	}
	if !h.fallbackHopAllowed(rc, orgCfg, providerID, model, selected...) {
		return models.ReasonPolicy
	}
	if ok, err := h.reserveAdd(rc, providerID, model); !ok || err != nil {
		return models.ReasonSkippedInsufficientCredits
	}
	return ""
}
