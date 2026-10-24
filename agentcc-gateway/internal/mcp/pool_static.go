package mcp

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// BuildStaticPool connects to every upstream server in the gateway's YAML
// config and returns the clients that connected; a server that fails is
// logged and skipped. Each connect is bounded by 30 s and its context is
// cancelled once Connect returns; a stdio child outlives that context.
func BuildStaticPool(ctx context.Context, cfg config.MCPConfig) []*Client {
	var clients []*Client
	for id, serverCfg := range cfg.Servers {
		client := NewClient(ClientConfig{
			ServerID:      id,
			URL:           serverCfg.URL,
			Command:       serverCfg.Command,
			Args:          serverCfg.Args,
			TransportType: serverCfg.Transport,
			Auth: AuthConfig{
				Type:   serverCfg.Auth.Type,
				Token:  serverCfg.Auth.Token,
				Header: serverCfg.Auth.Header,
				Key:    serverCfg.Auth.Key,
			},
			ToolsCacheTTL: serverCfg.ToolsCacheTTL,
		})
		connectCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
		if err := client.Connect(connectCtx); err != nil {
			slog.Error("failed to connect to MCP server", "server", id, "error", err)
			cancel()
			continue
		}
		cancel()
		clients = append(clients, client)
	}
	return clients
}
