package tenancy

import (
	"context"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// The transport also fences initialization, listing, pings and reconnects.
// A retained client never refreshes itself into a different credential version.
type personalHeaders struct {
	auth   mcp.AuthOverride
	server string
}

func (p personalHeaders) Header(ctx context.Context) (http.Header, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if userExpired(p.auth.Lifetime, p.auth.ExpiresAt, time.Now()) {
		return nil, mcp.CredentialRequired(p.server, "needs_reauth")
	}
	if p.auth.Check != nil {
		if err := p.auth.Check(ctx); err != nil {
			return nil, err
		}
	}
	if userExpired(p.auth.Lifetime, p.auth.ExpiresAt, time.Now()) {
		return nil, mcp.CredentialRequired(p.server, "needs_reauth")
	}
	return p.auth.Header.Clone(), nil
}
