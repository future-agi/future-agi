package main

import (
	"context"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs"
)

// initMCPOAuth is the MCP wiring step "oauth" (mcpWiringOrder, M23). With
// mcp.oauth.enabled it makes the MCP endpoint an OAuth 2.1 resource server:
// the bearer verifier accepts the authorization server's access tokens for
// exactly the virtual server they were issued for, and every MCP 401 names
// the protected-resource metadata of the address called. The addresses come
// from mcp.public_url and mcp.oauth.issuer; a gateway with OAuth on and
// either missing or unusable refuses to start rather than advertise a wrong
// address. The verification keys arrive by PUT /-/mcp/oauth/keys and are
// shared through Redis when it is configured. With OAuth off it changes
// nothing: JWTs stay refused and the 401 is the plain challenge. Owner: M23.
func initMCPOAuth(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled || !d.cfg.MCP.ClientOAuthEnabled || !d.cfg.MCP.OAuth.Enabled {
		return nil
	}
	res, err := oauthrs.NewResource(oauthrs.ConfigFrom(d.cfg.MCP))
	if err != nil {
		return err
	}
	var store oauthrs.KeyStore
	if d.redis != nil {
		store = oauthrs.NewRedisKeyStore(d.redis, "")
	} else {
		slog.Warn("mcp oauth: verification keys kept in memory; without Redis each replica needs its own key push")
	}
	keys := oauthrs.NewKeySet(res.Issuer(), store, nil)
	if err := keys.Load(context.Background()); err != nil {
		slog.Warn("mcp oauth: cannot read the stored verification keys yet; they are reread on use", "error", err)
	}
	deps.BearerVerifier = oauthrs.NewVerifier(res, keys, nil)
	deps.ResourceMetadataURL = res.MetadataURL("")
	slog.Info("mcp oauth resource server on", "resource", res.ResourceURI(""), "issuer", res.Issuer())
	return nil
}
