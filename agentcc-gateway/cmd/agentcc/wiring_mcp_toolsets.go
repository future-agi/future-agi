package main

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// initMCPToolsets is the MCP wiring step "toolsets" (mcpWiringOrder). Step
// tool sets and search mode are settings of each virtual server,
// read from the org's config on every request (mode, step_profiles), and the
// meta-tools are served by the chain's own steps (hook_metatool.go,
// hook_list_toolset.go), so there is no process-wide dependency to install:
// it sets nothing.
func initMCPToolsets(d *wiringDeps, deps *mcp.Deps) error { return nil }
