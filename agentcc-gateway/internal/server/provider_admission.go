package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// admitProvider uses a traversal's registry snapshot and canonical configured ID.
// Adapter identity chooses credentials; it never becomes a scope key.
func (h *Handlers) admitProvider(ctx context.Context, registry *routing.CircuitBreakerRegistry, rc *models.RequestContext, id string, p providers.Provider) (*routing.ProbeLease, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var cb *routing.CircuitBreaker
	if registry.IsEnabled() {
		if global, ok := h.registry.GetProvider(id); ok && global == p {
			cb = registry.Get(id)
		} else if orgID := rc.Metadata["org_id"]; orgID != "" {
			cb = registry.GetScoped(orgID, id)
		}
	}
	lease, allowed := cb.Acquire()
	if !allowed {
		return nil, routing.CircuitOpenError()
	}
	return lease, nil
}

func (h *Handlers) callProvider(ctx context.Context, registry *routing.CircuitBreakerRegistry, rc *models.RequestContext, p providers.Provider, call func() error) error {
	lease, err := h.admitProvider(ctx, registry, rc, rc.Provider, p)
	if err != nil {
		return err
	}
	defer lease.Abandon()
	err = call()
	lease.Complete(ctx, err)
	return err
}

func (h *Handlers) modelFallbackChain(rc *models.RequestContext) []string {
	if len(rc.OrgModelFallbacks) > 0 {
		return rc.OrgModelFallbacks[rc.Model]
	}
	if mf := h.modelFallbacks.Load(); mf != nil {
		return mf.GetChain(rc.Model)
	}
	return nil
}

// callModelFallbacks skips only locally refused candidates. The caller explicitly
// supplies the entry point's ordinary-error policy, independently of admission.
func (h *Handlers) callModelFallbacks(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, firstErr error, ordinaryEligible bool, call func(providers.Provider) error) error {
	if ctx.Err() != nil || (!routing.IsAdmissionRefusal(firstErr) && !ordinaryEligible) {
		return firstErr
	}
	originalModel, originalProvider := rc.Request.Model, rc.Provider
	originalMatch := rc.Metadata["org_provider_model_match"]
	for _, model := range h.modelFallbackChain(rc) {
		excluded := make(map[string]bool)
		for ctx.Err() == nil {
			p, id, resolvedModel, ok := h.resolveFallbackProviderExcluding(ctx, rc.Metadata["org_id"], orgCfg, rc, model, excluded)
			if !ok || ctx.Err() != nil {
				break
			}
			rc.Provider, rc.Request.Model = id, resolvedModel
			err := call(p)
			if err == nil {
				rc.Flags.FallbackUsed = true
				rc.Metadata["original_model"] = originalModel
				rc.Metadata["fallback_model"] = resolvedModel
				return nil
			}
			if !routing.IsCircuitOpen(err) {
				break
			}
			excluded[id] = true
		}
	}
	rc.Request.Model, rc.Provider = originalModel, originalProvider
	rc.Metadata["org_provider_model_match"] = originalMatch
	return firstErr
}
