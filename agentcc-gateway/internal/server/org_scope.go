package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A key limited to its org's providers (orgProvidersOnly) is served only by
// one of its org's own providers, built with the org's credentials, that
// lists the model asked for. Every path that picks a provider for such a key
// applies that rule or refuses.

// errProviderNotPermitted reports that a key limited to its org's providers
// asked for a provider its org does not hold for the model.
var errProviderNotPermitted = errors.New("provider not available for this API key")

// orgProviderByID returns the org's own build of providerID for model: the
// org's provider of that ID must be enabled, hold credentials and list model.
// A provider that cannot be built is reported as orgProviderError; any other
// miss is errProviderNotPermitted.
func (h *Handlers) orgProviderByID(orgID string, orgCfg *tenant.OrgConfig, providerID, model string) (providers.Provider, error) {
	if orgID == "" || orgCfg == nil || h.orgProviderCache == nil {
		return nil, errProviderNotPermitted
	}
	pc := orgCfg.Providers[providerID]
	if pc == nil || !pc.Enabled || !pc.HasCredentials() {
		return nil, errProviderNotPermitted
	}
	for _, m := range pc.Models {
		if !orgModelMatches(m, model, providerID) {
			continue
		}
		p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pc.APIKey, pc)
		if err != nil {
			return nil, orgProviderError(model, providerID, err)
		}
		return p, nil
	}
	return nil, errProviderNotPermitted
}

// lockedProvider returns the provider an x-agentcc-provider-lock header
// names. A lock only narrows the choice: for a key limited to its org's
// providers the target is looked up among the org's own providers for the
// model (never the global registry, whose providers carry the gateway's
// credentials), and a target the org does not hold for it is refused.
// Other keys lock to the global provider.
func (h *Handlers) lockedProvider(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, lockTarget string) (providers.Provider, error) {
	if h.orgProvidersOnly(ctx, rc) {
		p, err := h.orgProviderByID(orgID, orgCfg, lockTarget, rc.Model)
		if errors.Is(err, errProviderNotPermitted) {
			return nil, models.ErrForbidden(fmt.Sprintf("provider %q is not available for this API key", lockTarget))
		}
		if err != nil {
			return nil, err
		}
		// Already the org's own build: skip applyOrgProviderOverride.
		rc.Metadata["org_provider_model_match"] = rc.Model
		return p, nil
	}
	p, ok := h.registry.GetProvider(lockTarget)
	if !ok {
		return nil, models.ErrNotFound("provider_not_found",
			fmt.Sprintf("Locked provider %q not found", lockTarget))
	}
	return p, nil
}

// useProviderFailover reports whether a request fails over across the global
// providers that serve model. A key limited to its org's providers (orgOnly)
// does not: failover picks among config.yaml providers, so such a key keeps
// the org's provider resolved for the request. Its model fallbacks still apply
// (resolveFallbackProvider).
func (h *Handlers) useProviderFailover(fo *routing.Failover, model string, orgOnly bool) bool {
	return !orgOnly && fo != nil && fo.IsEnabled() && h.registry.Router() != nil && h.registry.Router().HasTargets(model)
}

// errDeferredRunUnavailable refuses a scheduled completion to a key limited
// to its org's providers. Async jobs run through runNonStream on the provider
// that admitted the request, under the same org-only rule.
func errDeferredRunUnavailable(what string) *models.APIError {
	return &models.APIError{
		Status:  http.StatusBadRequest,
		Type:    models.ErrTypeInvalidRequest,
		Code:    "deferred_run_unavailable",
		Message: what + " is not available for API keys limited to their organization's providers; send the request synchronously",
	}
}
