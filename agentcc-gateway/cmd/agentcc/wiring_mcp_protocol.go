package main

import (
	"net/url"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// initMCPProtocol is the MCP wiring step "protocol" (mcpWiringOrder). It
// hands the MCP server its protocol settings: the revisions served
// (mcp.protocol.versions), the body cap (mcp.max_body_bytes), the browser
// origins accepted (mcp.allowed_origins, plus the host of mcp.public_url as
// the gateway's own) and the list-cursor codec, sealed with the MCP state
// keys (AGENTCC_MCP_STATE_KEYS or mcp.state_keys, which config load requires
// whenever MCP is on). Nothing is set when MCP is disabled.
func initMCPProtocol(d *wiringDeps, _ *mcp.Deps) error {
	if d.cfg == nil || !d.cfg.MCP.Enabled {
		return nil
	}
	m := d.cfg.MCP
	codec, err := mcp.NewStateCodec(m.StateKeys, m.DevInsecureState)
	if err != nil {
		return err
	}
	set := protocol.Settings{
		Versions:       m.Protocol.Versions,
		MaxBodyBytes:   m.MaxBodyBytes,
		AllowedOrigins: m.AllowedOrigins,
		OwnHosts:       ownHosts(m.PublicURL),
		Cursors:        codec,
	}
	d.afterServer(func() error {
		if d.server == nil {
			return nil
		}
		if srv := d.server.MCP(); srv != nil {
			srv.SetProtocol(set)
		}
		return nil
	})
	return nil
}

// ownHosts is the host[:port] of the gateway's public URL, the one DNS name
// an Origin may always use, or none when the URL is unset or has no host.
func ownHosts(publicURL string) []string {
	u, err := url.Parse(publicURL)
	if publicURL == "" || err != nil || u.Host == "" {
		return nil
	}
	return []string{u.Host}
}
