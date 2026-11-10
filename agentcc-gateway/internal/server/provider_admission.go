package server

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// admitProvider runs only at the upstream boundary, after credentials are resolved.
// The returned completion must be called once, at the end of the call or stream.
func (h *Handlers) admitProvider(ctx context.Context, rc *models.RequestContext, p providers.Provider) (func(error), error) {
	registry := h.circuitBreakers.Load()
	if !registry.IsEnabled() {
		return func(error) {}, nil
	}
	var cb *routing.CircuitBreaker
	if global, ok := h.registry.GetProvider(p.ID()); ok && global == p {
		cb = registry.Get(p.ID())
	} else {
		cb = registry.GetScoped(rc.Metadata["org_id"], p.ID(), p)
	}
	generation, allowed := cb.Allow()
	if !allowed {
		return nil, routing.CircuitOpenError()
	}
	return func(err error) {
		if errors.Is(ctx.Err(), context.Canceled) || errors.Is(err, context.Canceled) {
			// Client disconnects and local aborts release the slot without claiming recovery.
			cb.RecordFailure(generation, nil)
		} else if err != nil {
			cb.RecordFailure(generation, err)
		} else {
			cb.RecordSuccess(generation)
		}
	}, nil
}

func (h *Handlers) callProvider(ctx context.Context, rc *models.RequestContext, p providers.Provider, call func() error) error {
	complete, err := h.admitProvider(ctx, rc, p)
	if err != nil {
		return err
	}
	err = call()
	complete(err)
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

// callModelFallbacks skips refused targets, not the entire fallback model.
// call owns admission so both ordinary calls and streams share this selection loop.
func (h *Handlers) callModelFallbacks(rc *models.RequestContext, orgCfg *tenant.OrgConfig, firstErr error, call func(providers.Provider) error) error {
	fo := h.effectiveFailover(orgCfg)
	if !routing.IsCircuitOpen(firstErr) && (fo == nil || !fo.ShouldFailover(firstErr)) {
		return firstErr
	}
	originalModel := rc.Request.Model
	for _, model := range h.modelFallbackChain(rc) {
		excluded := make(map[string]bool)
		for {
			p, id, resolvedModel, ok := h.resolveFallbackProviderExcluding(rc.Metadata["org_id"], orgCfg, rc, model, excluded)
			if !ok {
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
	rc.Request.Model = originalModel
	return firstErr
}
