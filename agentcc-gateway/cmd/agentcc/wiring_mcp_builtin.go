package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// initMCPBuiltin is the MCP wiring step "builtin" (mcpWiringOrder); it sets nothing.
func initMCPBuiltin(d *wiringDeps, deps *mcp.Deps) error { return nil }
