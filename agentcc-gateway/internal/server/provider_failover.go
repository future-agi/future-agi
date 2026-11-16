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

// executeProviderFailover resolves each canonical target once. Admission and
// sequential retries use that adapter and the traversal's registry snapshot.
// A stream receiver takes ownership only after successful startup.
func (h *Handlers) executeProviderFailover(ctx context.Context, rc *models.RequestContext, fo *routing.Failover, orgCfg *tenant.OrgConfig, call func(context.Context, string, providers.Provider, string) error, stream ...**routing.ProbeLease) (*routing.FailoverResult, error) {
	resolved := make(map[string]providers.Provider)
	registry := fo.CircuitBreakers()
	scoped := fo.WithAdmission(func(callCtx context.Context, id string) (*routing.ProbeLease, error) {
		if err := callCtx.Err(); err != nil {
			return nil, err
		}
		p, ok := h.registry.GetProvider(id)
		if !ok {
			return nil, models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("provider %q not found", id))
		}
		base := p
		// The selected target is new; metadata from an earlier model does not decide
		// whether its configured org credentials apply.
		if rc.Metadata["key_type"] != "internal" {
			p = h.applyOrgProviderOverride(rc.Metadata["org_id"], orgCfg, id, p)
		}
		// Preserve the existing explicit provider-lock entry policy. Ordinary
		// org-primary traversal cannot introduce an operator credential.
		if h.orgProvidersOnly(callCtx, rc) && rc.Metadata["provider_locked"] != "true" && p == base {
			return nil, h.unavailableModelError(rc, rc.Model)
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
