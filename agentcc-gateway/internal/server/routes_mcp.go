package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/mcpsec"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// mcpRouteStubs are the MCP route files, registered by registerMCPRoutes in
// this order once the MCP server is built. Each concern's lane owns its
// routes_mcp_<concern>.go file; adding one here is an integrator change.
var mcpRouteStubs = []routeStub{
	{"mcp_admin", (*Server).registerMCPAdminRoutes},
	{"mcp_vs", (*Server).registerMCPVSRoutes},
	{"mcp_legacy_sse", (*Server).registerMCPLegacySSERoutes},
	{"mcp_probe", (*Server).registerMCPProbeRoutes},
	{"mcp_revisions", (*Server).registerMCPRevisionsRoutes},
	{"mcp_oauth", (*Server).registerMCPOAuthRoutes},
	{"mcp_credentials", (*Server).registerMCPCredentialsRoutes},
	{"mcp_approvals", (*Server).registerMCPApprovalsRoutes},
}

// MCP returns the MCP server, or nil when MCP is disabled. The wiring hands it
// its dependencies (mcp.Server.SetDeps).
func (s *Server) MCP() *mcp.Server { return s.mcp }

// mcpEndpoint is the configured MCP endpoint, /mcp by default.
func mcpEndpoint(cfg *config.Config) string {
	if cfg.MCP.Endpoint == "" {
		return "/mcp"
	}
	return cfg.MCP.Endpoint
}

// mcpTimeoutPrefixes are the paths the default request timeout must not cut:
// the MCP endpoint (its GET stream lives for the session) and the OAuth
// protected-resource metadata under it. MCP bounds each call itself
// (tool_call_timeout). None when MCP is disabled.
func mcpTimeoutPrefixes(cfg *config.Config) []string {
	if !cfg.MCP.Enabled {
		return nil
	}
	endpoint := mcpEndpoint(cfg)
	return []string{endpoint, endpoint + "/", "/.well-known/oauth-protected-resource/"}
}

// mcpGuardConfig is the MCP tool-call guard's configuration from mcp.guardrails,
// with the gateway's legacy name separator (mcp.separator, M10 IR-2): the guard
// matches a blocked_tools entry <server_id><separator><tool> against the legacy
// exposed name, a virtual server's <server_id>__<tool> and the resolved
// upstream tool, so a custom separator cannot let a blocked tool through.
func mcpGuardConfig(m config.MCPConfig) map[string]interface{} {
	return map[string]interface{}{
		"separator":        m.Separator,
		"blocked_tools":    m.Guardrails.BlockedTools,
		"allowed_servers":  m.Guardrails.AllowedServers,
		"validate_inputs":  m.Guardrails.ValidateInputs,
		"validate_outputs": m.Guardrails.ValidateOutputs,
		"custom_patterns":  m.Guardrails.CustomPatterns,
		"tool_rate_limits": m.Guardrails.ToolRateLimits,
	}
}

// registerMCPRoutes builds the MCP server from cfg.MCP and registers its
// routes: POST, GET and DELETE on the endpoint, the five /-/mcp/* admin
// routes, then each MCP route file in mcpRouteStubs order. It does nothing
// when MCP is disabled.
func (s *Server) registerMCPRoutes(router *Router) {
	cfg := s.cfg
	if !cfg.MCP.Enabled {
		return
	}
	endpoint := mcpEndpoint(cfg)
	mcpServer := mcp.NewServer(mcp.ServerConfig{
		MaxAgentDepth:   cfg.MCP.MaxAgentDepth,
		ToolCallTimeout: cfg.MCP.ToolCallTimeout,
		SessionTTL:      cfg.MCP.SessionTTL,
		Separator:       cfg.MCP.Separator,
	})
	s.mcp = mcpServer

	// Attach per-key tool filtering if auth is configured.
	if cfg.Auth.Enabled && s.keyStore != nil {
		mcpServer.SetKeyAuth(&mcpKeyAuth{keyStore: s.keyStore})
		slog.Info("mcp per-key tool filtering enabled")
	}

	// Attach MCP tool call guardrail if configured.
	if cfg.MCP.Guardrails.Enabled {
		mcpServer.SetGuard(mcpsec.NewToolGuard(mcpGuardConfig(cfg.MCP)))
		slog.Info("mcp guardrails enabled",
			"blocked_tools", len(cfg.MCP.Guardrails.BlockedTools),
			"allowed_servers", len(cfg.MCP.Guardrails.AllowedServers),
			"custom_patterns", len(cfg.MCP.Guardrails.CustomPatterns),
			"tool_rate_limits", len(cfg.MCP.Guardrails.ToolRateLimits),
		)
	}

	// Set tool version/deprecation info from config.
	if len(cfg.MCP.ToolVersions) > 0 {
		versions := make(map[string]mcp.ToolVersionInfo, len(cfg.MCP.ToolVersions))
		for name, vc := range cfg.MCP.ToolVersions {
			versions[name] = mcp.ToolVersionInfo{
				Version:            vc.Version,
				Deprecated:         vc.Deprecated,
				DeprecationMessage: vc.DeprecationMessage,
				ReplacedBy:         vc.ReplacedBy,
			}
		}
		mcpServer.Registry().SetToolVersions(versions)
		slog.Info("mcp tool versions configured", "count", len(versions))
	}

	// Connect to upstream MCP servers; a stdio server's stderr goes through
	// the operator's redactor from its first line (M9 IR-12).
	for _, client := range mcp.BuildStaticPool(context.Background(), cfg.MCP, mcp.OperatorRedactor(cfg.Privacy)) {
		mcpServer.RegisterClient(client)
	}

	router.Handle("POST", endpoint, mcpServer.HandlePost)
	router.Handle("GET", endpoint, mcpServer.HandleSSE)
	router.Handle("DELETE", endpoint, mcpServer.HandleDelete)

	// Admin endpoints for MCP.
	router.Handle("GET", "/-/mcp/status", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		status := map[string]interface{}{
			"enabled":   true,
			"sessions":  mcpServer.SessionCount(),
			"tools":     mcpServer.Registry().ToolCount(),
			"resources": mcpServer.Registry().ResourceCount(),
			"prompts":   mcpServer.Registry().PromptCount(),
			"servers":   mcpServer.ClientStatuses(),
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(status)
	})
	router.Handle("GET", "/-/mcp/tools", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		tools := mcpServer.Registry().ListToolsJSON()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(tools)
	})

	// Tool execution playground endpoint.
	router.Handle("POST", "/-/mcp/test", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		var req struct {
			Name      string                 `json:"name"`
			Arguments map[string]interface{} `json:"arguments"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid request body"})
			return
		}
		if req.Name == "" {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "tool name is required"})
			return
		}
		result := mcpServer.TestTool(r.Context(), req.Name, req.Arguments)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})

	// Admin endpoints for MCP resources and prompts.
	router.Handle("GET", "/-/mcp/resources", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		resources := mcpServer.Registry().ListResources()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resources)
	})
	router.Handle("GET", "/-/mcp/prompts", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		prompts := mcpServer.Registry().ListPrompts()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(prompts)
	})

	// Per-concern MCP routes, each in its own routes_mcp_<concern>.go file.
	for _, rs := range mcpRouteStubs {
		rs.register(s, router)
	}

	slog.Info("mcp gateway enabled",
		"endpoint", endpoint,
		"servers", len(cfg.MCP.Servers),
		"max_depth", cfg.MCP.MaxAgentDepth,
	)
}
