package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// initMCPPolicy is the MCP wiring step "policy" (mcpWiringOrder); it sets nothing.
func initMCPPolicy(d *wiringDeps, deps *mcp.Deps) error { return nil }
