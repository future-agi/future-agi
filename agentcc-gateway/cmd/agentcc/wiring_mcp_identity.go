package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// initMCPIdentity is the MCP wiring step "identity" (mcpWiringOrder, M8). It
// installs the gateway's caller authentication on the MCP server from an
// afterServer callback, whatever auth.enabled says: the key store (built with
// request auth off too), mcp.allow_anonymous_dev, and whether request auth
// is off. The server's routes attach a key authenticator only when request
// auth is on; this replaces it, so a credential that is sent is checked on
// every gateway. With request auth off, a caller with no credential is served
// the operator's servers as on the release line (MCP-APPS-PLAN ruling 7)
// while mcp.static.org_id binds them to no org, and is 401 on a virtual
// server or once they are bound to one (mcp.Identity.KeylessStatic). The
// callbacks run before the server starts serving.
func initMCPIdentity(d *wiringDeps, _ *mcp.Deps) error {
	d.afterServer(func() error {
		if d.server == nil {
			return nil
		}
		srv := d.server.MCP()
		if srv == nil { // MCP disabled
			return nil
		}
		id := mcp.Identity{Keys: d.keyStore}
		if d.cfg != nil {
			id.AllowAnonymousDev = d.cfg.MCP.AllowAnonymousDev
			id.KeylessStatic = !d.cfg.Auth.Enabled
		}
		if id.AllowAnonymousDev {
			slog.Warn("mcp.allow_anonymous_dev is on: MCP requests without a credential are served with no principal; never use it outside tests")
		}
		srv.SetIdentity(id)
		return nil
	})
	return nil
}
