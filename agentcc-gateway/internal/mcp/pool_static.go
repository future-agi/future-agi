package mcp

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// BuildStaticPool connects to every upstream server in the gateway's YAML
// config and returns the clients that connected; a server that fails is
// logged and skipped (the tenancy's static pool retries it). The route setup
// runs it before the wiring hands the server its dependencies, so it uses
// today's client; the static pool adopts these clients on its first build
// and builds every later one through Deps.ClientFactory (M9). Each connect
// is bounded by 30 s and its context is cancelled once Connect returns (M5
// makes that harmless for stdio). redactor is the operator's log redactor for
// a stdio server's stderr (OperatorRedactor; nil for none): the route setup
// passes the same one the tenancy's later builds use (M9 IR-12, at the W3b
// merge), so a stdio server adopted at boot never logs what the operator's
// patterns hide.
func BuildStaticPool(ctx context.Context, cfg config.MCPConfig, redactor *privacy.Redactor) []*Client {
	var clients []*Client
	for id, serverCfg := range cfg.Servers {
		clientCfg, err := StaticPoolClientConfig(id, serverCfg, cfg, redactor)
		if err != nil {
			slog.Error("mcp server refused", "server", id, "error", err)
			continue
		}
		client := NewClient(clientCfg)
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

// StaticPoolClientConfig is the client config the static pool builds one
// YAML server with: M13's StaticClientConfig (its upstream, credential, era,
// call settings (timeout_ms, forward_headers, mcp.max_result_bytes), tool
// cache and declared stdio environment), the one place those keys are read
// (M13 IR-1, orchestrator ruling 4 at the W3b merge), plus the operator's
// mcp.stdio settings for a stdio server (M5's fields). A stdio server is
// refused while mcp.stdio.enabled is false. redactor is the operator's log
// redactor for stdio stderr, nil for none.
func StaticPoolClientConfig(id string, sc config.MCPServerConfig, m config.MCPConfig, redactor *privacy.Redactor) (ClientConfig, error) {
	cfg := StaticClientConfig(id, sc, m)
	if sc.Transport != "stdio" {
		return cfg, nil
	}
	if !m.Stdio.Enabled {
		return ClientConfig{}, fmt.Errorf("stdio server %q refused: mcp.stdio.enabled is false", id)
	}
	launcher, err := StdioLauncherFor(m.Stdio.Launcher)
	if err != nil {
		return ClientConfig{}, fmt.Errorf("stdio server %q: %w", id, err)
	}
	cfg.Launcher = launcher
	cfg.BaseEnv = m.Stdio.BaseEnv // nil keeps the default; an explicit [] means none
	cfg.StdioGrace = m.Stdio.Grace
	cfg.MaxMessageBytes = int(m.Stdio.MaxMessageBytes)
	cfg.StderrMaxLineBytes = m.Stdio.StderrMaxLineBytes
	cfg.StderrRedactor = redactor
	return cfg, nil
}

// OperatorRedactor is the operator's log redactor (the one main builds for
// request logs: privacy.mode and privacy.redact_patterns), applied to static
// stdio servers' stderr; nil when privacy is off. The route setup and the
// tenancy wiring both build theirs here.
func OperatorRedactor(p config.PrivacyConfig) *privacy.Redactor {
	if !p.Enabled {
		return nil
	}
	patterns := make([]privacy.PatternConfig, len(p.Patterns))
	for i, pat := range p.Patterns {
		patterns[i] = privacy.PatternConfig{Name: pat.Name, Pattern: pat.Pattern}
	}
	return privacy.New(p.Mode, patterns)
}

// AdaptClient serves a connected *Client as an UpstreamClient, the way
// DefaultClientFactory does.
func AdaptClient(c *Client) UpstreamClient { return &legacyClient{c: c} }

// StaticClients returns the clients registered on the server (the ones
// BuildStaticPool connected at route setup), by server id, for the static
// pool to adopt instead of connecting each server a second time.
func (s *Server) StaticClients() map[string]UpstreamClient {
	s.clientsMu.RLock()
	defer s.clientsMu.RUnlock()
	out := make(map[string]UpstreamClient, len(s.clients))
	for id, c := range s.clients {
		out[id] = AdaptClient(c)
	}
	return out
}

// SetStaticClient publishes the static pool's current client of a YAML
// server to the server's own registry, which today's hooks and Shutdown
// read, and reports whether the server now owns it: a client of today's
// kind replaces the registered one, and Shutdown closes it and waits; any
// other client (M13's), or nil, unregisters the server and stays the pool's.
// The pool closes the client it replaced.
func (s *Server) SetStaticClient(id string, c UpstreamClient) (owned bool) {
	if lc, ok := c.(*legacyClient); ok {
		s.clientsMu.RLock()
		same := s.clients[id] == lc.c
		s.clientsMu.RUnlock()
		if same { // the adopted client, already registered
			return true
		}
		s.RegisterClient(lc.c)
		s.notifyToolsChanged()
		return true
	}
	s.clientsMu.Lock()
	_, registered := s.clients[id]
	delete(s.clients, id)
	s.clientsMu.Unlock()
	if registered {
		s.registry.UnregisterServer(id)
		s.notifyToolsChanged()
	}
	return false
}
