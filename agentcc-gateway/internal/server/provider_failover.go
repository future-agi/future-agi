package server

import (
	"context"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgTargetOverride resolves org credentials lazily. An unhealthy operator target
// is eligible only when we can dispatch that *same* independent adapter. Cache
// failed resolutions too so a retry cannot change the credential decision.
type orgTargetOverride func(string) providers.Provider

func (h *Handlers) targetOverrides(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig) orgTargetOverride {
	if rc.Metadata["key_type"] == "internal" || orgID == "" || orgCfg == nil || len(orgCfg.Providers) == 0 || h.orgProviderCache == nil {
		return nil
	}
	cache := make(map[string]providers.Provider)
	return func(id string) providers.Provider {
		if ctx.Err() != nil || !providerAllowedByLock(rc, id) {
			return nil
		}
		if p, ok := cache[id]; ok {
			return p
		}
		p := h.applyOrgProviderOverride(orgID, orgCfg, id, nil)
		cache[id] = p
		return p
	}
}

func (o orgTargetOverride) apply(id string, base providers.Provider) providers.Provider {
	if o != nil {
		if p := o(id); p != nil {
			return p
		}
	}
	return base
}

func (o orgTargetOverride) healthException() func(string) bool {
	if o == nil {
		return nil
	}
	return func(id string) bool { return o(id) != nil }
}

// executeProviderFailover resolves each canonical target once. Admission and
// sequential retries use that adapter and the traversal's registry snapshot.
// A stream receiver takes ownership only after successful startup.
func (h *Handlers) executeProviderFailover(ctx context.Context, rc *models.RequestContext, fo *routing.Failover, orgCfg *tenant.OrgConfig, call func(context.Context, string, providers.Provider, string) error, stream ...**routing.ProbeLease) (*routing.FailoverResult, error) {
	resolved := make(map[string]providers.Provider)
	registry := fo.CircuitBreakers()
	overrides := h.targetOverrides(ctx, rc, rc.Metadata["org_id"], orgCfg)
	scoped := fo.WithIndependentHealth(overrides.healthException()).WithAdmission(func(callCtx context.Context, id string) (*routing.ProbeLease, error) {
		if err := callCtx.Err(); err != nil {
			return nil, err
		}
		// A refused target must not consume an attempt or build org credentials;
		// continue selection until the accepted provider can be considered.
		if !providerAllowedByLock(rc, id) {
			return nil, routing.ExcludeTarget(routing.ProvidersUnavailableError("provider does not match the accepted provider lock"))
		}
		base, ok := h.registry.GetProvider(id)
		if !ok {
			return nil, models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("provider %q not found", id))
		}
		// The selected target is new; metadata from an earlier model does not decide
		// whether its configured org credentials apply.
		p := overrides.apply(id, base)
		// Preserve the existing explicit provider-lock entry policy. Ordinary
		// org-primary traversal cannot introduce an operator credential.
		if h.orgProvidersOnly(callCtx, rc) && rc.Metadata["provider_locked"] != "true" && p == base {
			return nil, routing.ExcludeTarget(h.unavailableModelError(rc, rc.Model))
		}
		resolved[id] = p
		return h.admitProvider(callCtx, registry, rc, id, p)
	})
	invoke := func(callCtx context.Context, id, model string) error {
		return call(callCtx, id, resolved[id], model)
	}
	if len(stream) > 0 {
		result, lease, err := scoped.ExecuteStream(ctx, rc.Model, invoke)
		if err == nil {
			*stream[0] = lease
		}
		return result, err
	}
	return scoped.Execute(ctx, rc.Model, invoke)
}
