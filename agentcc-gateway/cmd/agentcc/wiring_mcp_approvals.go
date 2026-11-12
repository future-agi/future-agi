package main

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Always install the shared provider when MCP is enabled. A missing/outage
// Redis client remains a refusing provider, never the no-approvals default.
func initMCPApprovals(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	codec, err := mcp.NewStateCodec(d.cfg.MCP.StateKeys, d.cfg.MCP.DevInsecureState)
	if err != nil {
		return err
	}
	deps.Approvals = mcp.NewApprovalManager(redisstate.NewMCPApprovalStore(d.redis), codec, deps.Clock)
	return nil
}
