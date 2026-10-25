package server

import (
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// BillingHooks is how the request path reaches managed billing without
// importing it. The credits wiring installs the real one with SetBilling.
type BillingHooks interface {
	// ReserveAdd takes an extra hold for a fallback hop to model on
	// providerID (RESERVE mode=add) and reports whether the hop may run.
	ReserveAdd(rc *models.RequestContext, providerID, model string) (bool, error)
	// Void releases the request's unsettled hold.
	Void(rc *models.RequestContext, reason string)
}

// billingHolder holds the BillingHooks, embedded in Handlers. Nil until
// SetBilling installs them: every hop may run and nothing is held.
type billingHolder struct {
	billing BillingHooks
}

// SetBilling installs the billing hooks. Call it before the server serves.
func (h *Handlers) SetBilling(b BillingHooks) {
	h.billing = b
}

// reserveAdd calls billing.ReserveAdd, reporting it to the hook observer.
// Without billing hooks every hop may run.
func (h *Handlers) reserveAdd(rc *models.RequestContext, providerID, model string) (bool, error) {
	h.observeHookAt(hookReserveAdd, rc, providerID, model)
	if h.billing == nil {
		return true, nil
	}
	return h.billing.ReserveAdd(rc, providerID, model)
}

// OrgDefaultMaxTokensEstimate is the org's stored
// managed_models.default_max_tokens_estimate, 0 when unset: the output the
// credits plugin holds a managed request with no max_tokens for, below the
// model's own max output. A stored value below billing.MinOrgDefaultMaxOut
// reads as that floor.
func (h *Handlers) OrgDefaultMaxTokensEstimate(orgID string) int {
	if h.tenantStore == nil || orgID == "" {
		return 0
	}
	cfg := h.tenantStore.Get(orgID)
	if cfg == nil || cfg.ManagedModels == nil || cfg.ManagedModels.DefaultMaxTokensEstimate == nil {
		return 0
	}
	return billing.OrgDefaultMaxOut(*cfg.ManagedModels.DefaultMaxTokensEstimate)
}
