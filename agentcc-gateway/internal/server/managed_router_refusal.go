package server

import (
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// routerCreditError is called only after resolution has found no usable BYOK
// route. Preserve the existing unavailable-model/provider wording and 403, but
// explain the billing contract gap rather than suggesting the caller's key is
// broken. It neither invents targets nor remaps vendor routers to gateway auto.
func (h *Handlers) routerCreditError(providerID, model, subject, named string) *models.APIError {
	m, ok := h.catalog.Load().Get(providerID, model)
	if !ok {
		return nil
	}
	reason := m.RouterCreditRefusal()
	if reason == "" {
		return nil
	}
	message := reason
	if !routerbilling.FamilyMember(model) {
		message = fmt.Sprintf("%s %q is not available for this API key on Future AGI credits: %s", subject, named, reason)
	}
	err := models.ErrForbidden(message)
	err.Code = "managed_router_unbounded"
	return err
}

func (h *Handlers) unavailableRouterCreditError(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) *models.APIError {
	if !orgCfg.ManagedModelsEnabled() || !managedEndpoint(rc) {
		return nil
	}
	cat := h.catalog.Load()
	p, upstream, ok := h.registry.ResolveManaged(model, func(providerID, id string) bool {
		_, listed := cat.Get(providerID, id)
		return listed
	})
	if !ok {
		return nil
	}
	return h.routerCreditError(p.ID(), upstream, "model", model)
}
