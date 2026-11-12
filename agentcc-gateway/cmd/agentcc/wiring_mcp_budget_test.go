package main

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"testing"
)

func TestW5bLimitsProviderWired(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = true
	cfg.MCP.StateKeys = []string{mcpTestStateKey}
	srv := newWiringServer(t, true)
	d := &wiringDeps{cfg: cfg, server: srv}
	if err := initMCP(d); err != nil {
		t.Fatal(err)
	}
	if err := d.startServerWiring(); err != nil {
		t.Fatal(err)
	}
	if _, ok := srv.MCP().Deps().RateLimiter.(*mcp.ToolLimits); !ok {
		t.Fatal("rate provider not installed")
	}
	if _, ok := srv.MCP().Deps().ToolBudget.(*mcp.ToolLimits); !ok {
		t.Fatal("count provider not installed")
	}
	cfg.MCP.Enabled = false
	deps := &mcp.Deps{}
	if err := initMCPBudget(d, deps); err != nil || deps.RateLimiter != nil || deps.ToolBudget != nil {
		t.Fatal("disabled MCP installed provider")
	}
}
