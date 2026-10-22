package main

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// initMCPProbe is the MCP wiring step "probe" (mcpWiringOrder). It installs
// the SSRF guard as deps.Dialer, built from the operator list
// mcp.egress.private_allow alone; the MCP probe dials through it. Without a
// config it builds the guard with no exceptions.
func initMCPProbe(d *wiringDeps, deps *mcp.Deps) error {
	var allow []string
	if d.cfg != nil {
		allow = d.cfg.MCP.Egress.PrivateAllow
	}
	guard, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		return err
	}
	deps.Dialer = guard
	return nil
}
