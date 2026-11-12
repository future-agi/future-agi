package main

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// initMCPSessions is the MCP wiring step "sessions" (mcpWiringOrder): legacy
// sessions are sealed with the state codec built from mcp.state_keys (or
// AGENTCC_MCP_STATE_KEYS), negotiate among the served 2025 revisions, and
// live in Redis when mcp.session_store is redis (the default once Redis is
// configured), else in memory. Redis configured but unreachable at startup
// leaves sessions in memory, as the gateway's other Redis state does: each
// replica then holds only its own sessions.
func initMCPSessions(d *wiringDeps, deps *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	cfg := d.cfg.MCP
	codec, err := mcp.NewStateCodec(cfg.StateKeys, cfg.DevInsecureState)
	if err != nil {
		return err
	}
	if cfg.SessionStore == config.MCPSessionStoreRedis {
		if d.redis != nil {
			deps.SessionStore = redisstate.NewMCPSessionStore(d.redis, "")
		} else {
			slog.Warn("mcp sessions kept in memory: mcp.session_store is redis but Redis is unavailable; a session works only on the replica that opened it")
		}
	}
	opts := mcp.SessionOptions{
		Codec:          codec,
		Versions:       cfg.Protocol.Versions,
		LegacyDisabled: !cfg.Protocol.LegacySessions,
	}
	d.afterServer(func() error {
		if d.server == nil {
			return nil
		}
		if srv := d.server.MCP(); srv != nil {
			srv.ConfigureSessions(opts)
		}
		return nil
	})
	return nil
}
