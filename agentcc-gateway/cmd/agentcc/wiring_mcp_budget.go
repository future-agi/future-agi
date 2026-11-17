package main

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Install even when Redis is absent: explicitly configured limits must
// refuse rather than falling back to independent replica-local counters.
// The provider does nothing unless a virtual server has configured limits.
func initMCPBudget(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	limits := mcp.NewToolLimits(redisstate.NewToolCallBudgetStore(d.redis))
	deps.RateLimiter = limits
	deps.ToolBudget = limits
	return nil
}
