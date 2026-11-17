package vendors

import (
	"context"
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// UnavailableRuntime asks the pool's admission gate and then delegates the
// credential operation when no K2 runtime is configured. It owns no admission
// policy.
type UnavailableRuntime struct{ base mcp.CredentialResolver }

var _ mcp.CredentialResolver = (*UnavailableRuntime)(nil)
var _ mcp.CredentialPurger = (*UnavailableRuntime)(nil)
var _ mcp.PushedGrantRevoker = (*UnavailableRuntime)(nil)

// NewUnavailableRuntime installs no runtime or credential source. A nil base
// preserves the default resolver's no-override behavior for ordinary servers.
func NewUnavailableRuntime(base mcp.CredentialResolver) *UnavailableRuntime {
	return &UnavailableRuntime{base: base}
}

func (r *UnavailableRuntime) Resolve(cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	return resolveConstructed(r.base, cc)
}

// Keep local invalidation available even while K2 itself cannot serve calls.
func (r *UnavailableRuntime) PurgeCredentials(ctx context.Context, org, user, server string) (int, error) {
	if purger, ok := r.base.(mcp.CredentialPurger); ok {
		return purger.PurgeCredentials(ctx, org, user, server)
	}
	return 0, errors.New("mcp vendors: no credential purger is configured")
}

// Unsupported revisions return an error; a legacy purge never acknowledges a fence.
func (r *UnavailableRuntime) RevokePushedGrants(ctx context.Context, org, server string, through int64) error {
	if revoker, ok := r.base.(mcp.PushedGrantRevoker); ok {
		return revoker.RevokePushedGrants(ctx, org, server, through)
	}
	return errors.New("mcp vendors: pushed grant revocation is unsupported")
}
