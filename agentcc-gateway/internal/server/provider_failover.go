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

// executeProviderFailover keeps operator breakers for operator instances and
// gives org (BYOK) instances a breaker per (org, provider). Resolve once so admission and retries use the same actual provider.
// call gets the routing ID as well as the instance: an org override instance has
// its own registry ID, but responses, logs, metrics and prices name the target.
func (h *Handlers) executeProviderFailover(ctx context.Context, rc *models.RequestContext, fo *routing.Failover, orgCfg *tenant.OrgConfig, call func(ctx context.Context, providerID string, p providers.Provider, modelOverride string) error) (*routing.FailoverResult, error) {
	resolved := make(map[string]providers.Provider)
	resolve := func(id string) providers.Provider {
		if p, ok := resolved[id]; ok {
			return p
		}
		p, ok := h.registry.GetProvider(id)
		if ok && shouldApplyOrgProviderOverride(rc) {
			p = h.applyOrgProviderOverride(rc.Metadata["org_id"], orgCfg, id, p)
		}
		resolved[id] = p
		return p
	}
	// Attempts on an org's own key use that org's breaker, not the operator's.
	scoped := fo.WithOrgCircuitBreakers(rc.Metadata["org_id"], func(id string) bool {
		p := resolve(id)
		base, ok := h.registry.GetProvider(id)
		return ok && p == base
	})
	return scoped.Execute(ctx, rc.Model, func(callCtx context.Context, id, model string) error {
		p := resolve(id)
		if p == nil {
			return models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("provider %q not found", id))
		}
		return call(callCtx, id, p, model)
	})
}
