package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgHasCredentials reports whether the org holds its own credentials for a
// provider ID, which applyOrgProviderOverride then uses in place of the
// global provider's.
func orgHasCredentials(orgCfg *tenant.OrgConfig, providerID string) bool {
	if orgCfg == nil {
		return false
	}
	pc := orgCfg.Providers[providerID]
	return pc != nil && pc.Enabled && pc.HasCredentials()
}

// providerForKey returns the instance of the global provider providerID that
// rc's key may call for model (the id sent upstream), and records how it is
// paid for. A key limited to its org's providers gets the org's own build of
// it when the org holds credentials for that ID — refused if it cannot be
// built, never the global instance in its place — or, failing that, the
// global instance only when the provider is managed, the org has opted in,
// and the managed call can be billed (managedServes). Any other key may use
// the global instance; the caller applies its usual org override.
func (h *Handlers) providerForKey(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, providerID string, global providers.Provider, model string) (providers.Provider, error) {
	if !h.orgProvidersOnly(ctx, rc) {
		return global, nil
	}
	if orgHasCredentials(orgCfg, providerID) && h.orgProviderCache != nil {
		pc := orgCfg.Providers[providerID]
		p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pc.APIKey, pc)
		if err != nil {
			return nil, orgProviderError(rc.Model, providerID, err)
		}
		rc.Metadata["billing_mode"] = billingBYOK
		return p, nil
	}
	if orgCfg.ManagedModelsEnabled() && h.registry.IsManaged(providerID) && managedEndpoint(rc) && h.managedServes(providerID, model) {
		rc.Metadata["billing_mode"] = billingManaged
		return global, nil
	}
	if orgCfg.ManagedModelsEnabled() && h.registry.IsManaged(providerID) && managedEndpoint(rc) {
		if err := h.routerCreditError(providerID, model, "provider", providerID); err != nil {
			return nil, err
		}
	}
	return nil, errProviderNotPermitted
}

// resolveManaged sends model to a managed provider when the org has opted in
// and one serves it. A name no managed provider takes as it is is tried as
// the catalogue id it names (managedCatalogID: "gpt-4o" for
// "openai/gpt-4o"). It points rc.Request at the model ID the provider takes.
func (h *Handlers) resolveManaged(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, bool) {
	if !orgCfg.ManagedModelsEnabled() || !managedEndpoint(rc) {
		return nil, "", false
	}
	p, upstream, ok := h.registry.ResolveManaged(model, h.catalogHas)
	if !ok {
		if id, _ := h.managedCatalogID(model); id != "" {
			p, upstream, ok = h.registry.ResolveManaged(id, h.catalogHas)
		}
	}
	if !ok || !h.managedServes(p.ID(), upstream) {
		return nil, "", false
	}
	rc.Provider = p.ID()
	rc.Metadata["billing_mode"] = billingManaged
	if upstream != model {
		rc.Metadata["managed_model"] = upstream
		if rc.Request != nil {
			rc.Request.Model = upstream
		}
	}
	return p, upstream, true
}

// resolveOnOrgKeyUnderManagedID sends model, which no managed provider
// serves on Future AGI's key (resolveManaged), to the org's own key when the
// managed resolver names a provider the org holds its own credentials for
// and that provider's upstream routes ids its catalogue does not list
// (catalog.RoutesUnlisted): "<managed id>/<model>" with a model the
// catalogue does not list, or a model it lists with no fixed price (a router
// such as openrouter/auto), by its id or behind that prefix. The call is
// built from the org's own credentials and refused if they cannot be built
// (providerForKey), so it is the org's own call (byok) and is never billed
// to Future AGI credits. ok is false when this does not apply; err is the
// refusal when it does.
func (h *Handlers) resolveOnOrgKeyUnderManagedID(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) (p providers.Provider, upstream string, ok bool, err error) {
	if !orgCfg.ManagedModelsEnabled() || !managedEndpoint(rc) || h.orgProviderCache == nil {
		return nil, "", false, nil
	}
	// Price availability does not change the identity of a listed BYOK call.
	cat := h.catalog.Load()
	global, upstream, found := h.registry.ResolveManaged(model, func(providerID, id string) bool {
		_, listed := cat.Get(providerID, id)
		return listed
	})
	if !found || !orgHasCredentials(orgCfg, global.ID()) || !h.catalog.Load().RoutesUnlisted(global.ID()) {
		return nil, "", false, nil
	}
	p, err = h.providerForKey(ctx, rc, rc.Metadata[tenant.MetadataKeyOrgID], orgCfg, global.ID(), global, upstream)
	if err != nil {
		return nil, "", true, err
	}
	rc.Provider = global.ID()
	if rc.Request != nil {
		rc.Request.Model = upstream
	}
	return p, upstream, true, nil
}

// managedServes reports whether a managed call of model, the id sent
// upstream, on providerID is served on Future AGI's key
// (catalog.ServesManaged): a provider serves only what its catalog lists at
// a supported current price, whatever the source and whichever path named it:
// the catalog, a configured model, a "<managed id>/<model>" prefix,
// a provider lock or a fallback.
func (h *Handlers) managedServes(providerID, model string) bool {
	return h.catalog.Load().ServesManaged(providerID, model)
}

// managedCreditAvailable reports whether the org may take a managed request
// it was not pre-checked for (a fallback): the credits plugin checked the
// balance only for the provider resolved before the pipeline ran.
func (h *Handlers) managedCreditAvailable(orgID string) bool {
	if h.orgLedger == nil || !h.requireManagedCredits || orgID == "" {
		return true
	}
	return h.orgLedger.BalanceMicros(orgID) > 0
}

// autoManagedProvider sends an auto-chosen managed-catalog model to the
// managed provider it was scored on, rather than letting name resolution
// read its "vendor/" prefix as a provider ID.
func (h *Handlers) autoManagedProvider(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, d *routing.AutoDecision) (providers.Provider, bool) {
	if d == nil || !managedEndpoint(rc) || !h.registry.IsManaged(d.ProviderID) || !h.catalogHas(d.ProviderID, d.Model) {
		return nil, false
	}
	global, ok := h.registry.GetProvider(d.ProviderID)
	if !ok {
		return nil, false
	}
	p, err := h.providerForKey(ctx, rc, rc.Metadata["org_id"], orgCfg, d.ProviderID, global, d.Model)
	if err != nil {
		return nil, false
	}
	rc.Provider = d.ProviderID
	return p, true
}
