package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// initMCPPins is the MCP wiring step "pins" (mcpWiringOrder): with MCP on,
// the tools of an org's own servers are pinned by the org's mcp.pinning
// (mcp.OrgPins). A tool whose definition is not the one an admin
// accepted is held until an admin accepts it; Django writes the pins.
func initMCPPins(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	deps.Pins = mcp.OrgPins{}
	return nil
}
