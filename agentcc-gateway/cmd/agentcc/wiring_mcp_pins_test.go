package main

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// TestMCPPinsWiring (M20): with MCP on, the "pins" step installs org pinning
// (mcp.OrgPins) as Deps.Pins, so a changed tool of an org's own server is held
// for review; with MCP off it installs nothing.
func TestMCPPinsWiring(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	var deps mcp.Deps
	if err := initMCPPins(&wiringDeps{cfg: cfg}, &deps); err != nil {
		t.Fatalf("initMCPPins: %v", err)
	}
	if _, ok := deps.Pins.(mcp.OrgPins); !ok {
		t.Fatalf("Deps.Pins = %T, want mcp.OrgPins", deps.Pins)
	}

	cfg.MCP.Enabled = false
	deps = mcp.Deps{}
	if err := initMCPPins(&wiringDeps{cfg: cfg}, &deps); err != nil {
		t.Fatalf("initMCPPins with MCP off: %v", err)
	}
	if deps.Pins != nil {
		t.Fatalf("Deps.Pins = %T with MCP off, want nothing installed", deps.Pins)
	}
}
