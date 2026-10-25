package server

import (
	"context"
	"errors"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func (h *Handlers) resolveFallbackProvider(ctx context.Context, orgID string, orgCfg *tenant.OrgConfig, rc *models.RequestContext, fallbackModel string) (providers.Provider, string, string, bool) {
	// The org's own provider is sent the id it registered (orgModelFor), as
	// the primary is; the request's own metadata stays the primary's.
	if orgProvider, orgProviderID, upstream, _, _ := h.orgRouteFor(orgID, orgCfg, fallbackModel); orgProvider != nil {
		rc.Metadata["billing_mode"] = billingBYOK
		return orgProvider, orgProviderID, upstream, true
	}

	// A key limited to its org's providers falls back only to a managed
	// model, or to a global provider ID it holds its own credentials for —
	// decided per fallback, not from the primary's org_provider_* flags.
	// The credit pre-check runs again for a managed fallback: an org is
	// served a managed fallback only while it has managed credit.
	if h.orgProvidersOnly(ctx, rc) {
		prevBilling := rc.Metadata["billing_mode"]
		delete(rc.Metadata, "managed_model")
		if h.managedCreditAvailable(orgID) {
			if p, upstream, ok := h.resolveManaged(rc, orgCfg, fallbackModel); ok {
				return p, p.ID(), upstream, true
			}
		}
		fbResult, err := h.registry.ResolveWithRouting(fallbackModel)
		if err != nil {
			return nil, "", fallbackModel, false
		}
		resolvedModel := fallbackModel
		if fbResult.ModelOverride != "" {
			resolvedModel = fbResult.ModelOverride
		}
		p, err := h.providerForKey(ctx, rc, orgID, orgCfg, fbResult.Provider.ID(), fbResult.Provider, resolvedModel)
		if err != nil {
			return nil, "", fallbackModel, false
		}
		if rc.Metadata["billing_mode"] == billingManaged && !h.managedCreditAvailable(orgID) {
			rc.Metadata["billing_mode"] = prevBilling
			return nil, "", fallbackModel, false
		}
		return p, fbResult.Provider.ID(), resolvedModel, true
	}

	fbResult, fbErr := h.registry.ResolveWithRouting(fallbackModel)
	if fbErr != nil {
		return nil, "", fallbackModel, false
	}
	fbProvider := fbResult.Provider
	if shouldApplyOrgProviderOverride(rc) {
		fbProvider = h.applyOrgProviderOverride(orgID, orgCfg, fbResult.Provider.ID(), fbResult.Provider)
	}
	resolvedModel := fallbackModel
	if fbResult.ModelOverride != "" {
		resolvedModel = fbResult.ModelOverride
	}
	return fbProvider, fbResult.Provider.ID(), resolvedModel, true
}

// useProviderFailover reports whether a request fails over across the global
// providers that serve its model. Only a key that may use any global provider
// does: failover picks among config.yaml providers, and a customer key reaches
// only providers it holds credentials for.
func (h *Handlers) useProviderFailover(ctx context.Context, fo *routing.Failover, rc *models.RequestContext) bool {
	return fo != nil && fo.IsEnabled() && h.registry.Router() != nil &&
		h.registry.Router().HasTargets(rc.Model) && !h.orgProvidersOnly(ctx, rc)
}

// shouldTryModelFallback reports whether err moves the request down its model
// fallback chain. An auto-routed request falls back on a rate limit or an
// upstream failure even when the org configured no failover: its runners-up
// are part of the decision.
func shouldTryModelFallback(fo *routing.Failover, rc *models.RequestContext, err error) bool {
	if fo != nil {
		return fo.ShouldFailover(err)
	}
	if rc.Metadata["auto_model"] == "" {
		return false
	}
	var apiErr *models.APIError
	return errors.As(err, &apiErr) && (apiErr.Status == http.StatusTooManyRequests || apiErr.Status >= 500)
}

// admitFallbackHop reports whether the request may move on to model on
// providerID after an attempt failed (call-order step 14): the hop must be
// allowed, then take its own hold. A hop it refuses is not dispatched.
func (h *Handlers) admitFallbackHop(rc *models.RequestContext, orgCfg *tenant.OrgConfig, providerID, model string) bool {
	if !h.fallbackHopAllowed(rc, orgCfg, providerID, model) {
		return false
	}
	ok, err := h.reserveAdd(rc, providerID, model)
	return ok && err == nil
}
