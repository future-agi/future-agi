package credentials

import (
	"context"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// PushedResolver owns shared static grant lifetimes without any OAuth token
// source or connect-link codec. Its narrow method set cannot fetch a token.
// Resolve leaves per_user calls without an override; step 13 still refuses them.
// SharedAuth refuses shared OAuth just like the default MCP dependencies.
type PushedResolver struct {
	lifetimes *Resolver
}

var (
	_ mcp.CredentialResolver = (*PushedResolver)(nil)
	_ mcp.SharedCredentials  = (*PushedResolver)(nil)
	_ mcp.PushedCredentials  = (*PushedResolver)(nil)
	_ mcp.PushedGrantRevoker = (*PushedResolver)(nil)
	_ mcp.CredentialPurger   = (*PushedResolver)(nil)
)

// NewPushedResolver reuses the pushed-grant admission, completion and revocation
// fences. Redis keeps their monotonic revoked/admitted revisions across replicas.
// Without Redis the same revision rules apply only within this process; they do
// not fence another replica or survive a process restart.
func NewPushedResolver(client *redisstate.Client) *PushedResolver {
	r := &Resolver{now: time.Now}
	if client != nil {
		// Only lifetime operations and purge use this cache, never sealed token
		// reads/writes, so there is no OAuth codec or token channel to configure.
		r.cache = NewTokenCache(client, nil, CacheOptions{})
	}
	return &PushedResolver{lifetimes: r}
}

func (*PushedResolver) Resolve(*mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	return nil, nil
}

func (*PushedResolver) SharedAuth(string, string) mcp.HeaderSource {
	return pushedOAuthOff{}
}

type pushedOAuthOff struct{}

func (pushedOAuthOff) Header(context.Context) (http.Header, error) {
	return nil, mcp.ErrSharedCredentialsOff
}

func (r *PushedResolver) PushedCredential(ctx context.Context, org, server string, grant mcp.PushedGrant) (*mcp.AuthOverride, error) {
	return r.lifetimes.PushedCredential(ctx, org, server, grant)
}

func (r *PushedResolver) RevokePushedGrants(ctx context.Context, org, server string, through int64) error {
	return r.lifetimes.RevokePushedGrants(ctx, org, server, through)
}

func (r *PushedResolver) PurgeCredentials(ctx context.Context, org, user, server string) (int, error) {
	return r.lifetimes.PurgeCredentials(ctx, org, user, server)
}
