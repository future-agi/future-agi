package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// initMCPVServer is the MCP wiring step "vserver" (mcpWiringOrder). Owner:
// M10. Once the MCP server exists it tells the registry mcp.static.org_id,
// so the legacy view (the operator's YAML servers) is served only to that
// org's principals; every other org, and a principal with no org, sees its
// own MCP config only (IC-R1-7). Until then, and with static.org_id unset, no
// org is the static one (fail closed), and a principal with no org gets the
// legacy view.
func initMCPVServer(d *wiringDeps, _ *mcp.Deps) error {
	d.afterServer(func() error {
		if d.server == nil || d.cfg == nil {
			return nil
		}
		if srv := d.server.MCP(); srv != nil {
			srv.Registry().SetStaticOrg(d.cfg.MCP.Static.OrgID)
		}
		return nil
	})
	return nil
}
