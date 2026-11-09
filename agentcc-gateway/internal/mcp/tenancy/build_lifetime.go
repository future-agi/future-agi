package tenancy

import (
	"context"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// constructionAuth runs before the factory. Entry retirement owns even the
// partially constructed shared/supplied connection. Every resolved shared grant
// also binds its exact distributed identity, just as personal construction does.
func constructionAuth(ctx context.Context, org string, sp spec, auth *mcp.AuthOverride, deps *mcp.Deps) (mcp.ClientConfig, *mcp.AuthOverride, func(), error) {
	cfg := sp.client
	cfg.Lifetime = ctx
	cleanup := func() {}
	var shared *mcp.AuthOverride
	if sp.sharedOAuth {
		if deps.SharedCredentials == nil {
			return cfg, nil, cleanup, mcp.ErrSharedCredentialsOff
		}
		source := deps.SharedCredentials.SharedAuth(org, sp.id)
		cfg.Auth = mcp.AuthConfig{Source: source}
		if provider, ok := source.(interface {
			Credential(context.Context) (*mcp.AuthOverride, error)
		}); ok {
			var err error
			shared, err = provider.Credential(ctx)
			if err != nil {
				return cfg, nil, cleanup, err
			}
			auth = shared
		}
	}
	if !sp.sharedOAuth && !sp.perUser && sp.credentialGrant != "" {
		if provider, ok := deps.SharedCredentials.(mcp.PushedCredentials); ok {
			var expiry time.Time
			var err error
			if sp.credentialExpiry != "" {
				expiry, err = time.Parse(time.RFC3339Nano, sp.credentialExpiry)
				if err != nil {
					return cfg, nil, cleanup, mcp.CredentialRequired(sp.id, "expired")
				}
			}
			shared, err = provider.PushedCredential(ctx, org, sp.id, mcp.PushedGrant{Identity: sp.credentialGrant, Revision: sp.credentialRevision, Expiry: expiry})
			if err != nil {
				return cfg, nil, cleanup, err
			}
			auth = shared
		}
	}
	if auth != nil {
		life, cancel := context.WithCancel(ctx)
		stop := func() bool { return false }
		if auth.Lifetime != nil {
			stop = context.AfterFunc(auth.Lifetime, cancel)
			if auth.Lifetime.Err() != nil {
				cancel()
			}
		}
		cleanup = func() { stop(); cancel() }
		cfg.Lifetime = life
		if len(auth.Header) > 0 {
			cfg.Auth = authFromOverride(*auth)
		}
		pinned := *auth
		pinned.Lifetime = life
		cfg.Auth.Source = personalHeaders{auth: pinned, server: sp.id}
	}
	return cfg, shared, cleanup, nil
}

// authFromOverride is the client credential of a per-call override: a
// bearer Authorization header, else a header with a nonempty first value as an api key.
func authFromOverride(a mcp.AuthOverride) mcp.AuthConfig {
	if v := a.Header.Get("Authorization"); len(v) > 7 && strings.EqualFold(v[:7], "Bearer ") {
		return mcp.AuthConfig{Type: "bearer", Token: v[7:]}
	}
	for name, vals := range a.Header {
		if len(vals) > 0 && vals[0] != "" {
			return mcp.AuthConfig{Type: "api_key", Header: name, Key: vals[0]}
		}
	}
	return mcp.AuthConfig{}
}
