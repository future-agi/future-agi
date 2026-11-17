package server

import (
	"context"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Bind before BeginInclusive persists dispatch ownership. Both the provider and
// journal use the same immutable context credential, even if an org rotates its
// pushed key while the request is streaming. BYOK never enters this path.
func (h *Handlers) bindInclusiveCredential(ctx context.Context, rc *models.RequestContext, providerID string, selected ...providers.Provider) (context.Context, error) {
	var p providers.Provider
	if len(selected) == 1 {
		p = selected[0]
	} else if h.registry != nil {
		p, _ = h.registry.GetProvider(providerID)
	}
	if p == nil || p.ID() != providerID {
		return ctx, fmt.Errorf("inclusive provider unavailable")
	}
	credential, ok := p.(interface {
		BindInclusiveCredential(context.Context) (context.Context, string)
	})
	if !ok {
		return ctx, fmt.Errorf("inclusive credential binding unavailable")
	}
	ctx = models.WithoutInclusiveCredential(ctx)
	scope := "shared"
	upstreamKeyID := "" // the pinned financial contract keeps shared keys empty
	mode := h.managedUpstreamKeyMode
	if mode == "per_org" || mode == "per_org_with_shared_fallback" {
		org := rc.Metadata["org_id"]
		var token string
		if h.tenantStore != nil {
			if cfg := h.tenantStore.Get(org); cfg != nil && cfg.ManagedModels != nil {
				if k := cfg.ManagedModels.UpstreamKeys[providerID]; k != nil {
					token = k.APIKey
					upstreamKeyID = k.KeyHash
				}
			}
		}
		if token != "" {
			ctx = models.WithInclusiveCredential(ctx, providerID, token)
			scope = org
		} else if mode == "per_org" {
			return ctx, fmt.Errorf("organization upstream credential unavailable")
		} else {
			// A retained management hash without its secret did not pay for
			// this invocation: shared fallback keeps the v1 audit field empty.
			upstreamKeyID = ""
		}
	}
	ctx, identity := credential.BindInclusiveCredential(ctx)
	if identity == "" {
		return ctx, fmt.Errorf("inclusive credential is empty")
	}
	rc.Billing.UpstreamKeyID = upstreamKeyID
	rc.Billing.InclusiveIdentity = models.InclusiveIdentity{Provider: providerID, Credential: identity, Scope: scope, SnapshotModels: rc.Billing.ReservePrice.Inclusive.SnapshotModels}
	return ctx, nil
}
