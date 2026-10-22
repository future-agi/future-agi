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
// this order once the MCP server is built. Each concern has its own
// routes_mcp_<concern>.go file.
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
// the MCP endpoint (its GET stream lives for the session), the paths under it
// and /.well-known/oauth-protected-resource/. MCP bounds each call itself
// (tool_call_timeout). None when MCP is disabled.
func mcpTimeoutPrefixes(cfg *config.Config) []string {
	if !cfg.MCP.Enabled {
		return nil
	}
	endpoint := mcpEndpoint(cfg)
	return []string{endpoint, endpoint + "/", "/.well-known/oauth-protected-resource/"}
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
		guardCfg := map[string]interface{}{
			"blocked_tools":    cfg.MCP.Guardrails.BlockedTools,
			"allowed_servers":  cfg.MCP.Guardrails.AllowedServers,
			"validate_inputs":  cfg.MCP.Guardrails.ValidateInputs,
			"validate_outputs": cfg.MCP.Guardrails.ValidateOutputs,
			"custom_patterns":  cfg.MCP.Guardrails.CustomPatterns,
			"tool_rate_limits": cfg.MCP.Guardrails.ToolRateLimits,
		}
		mcpServer.SetGuard(mcpsec.NewToolGuard(guardCfg))
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

	// Connect to upstream MCP servers.
	for _, client := range mcp.BuildStaticPool(context.Background(), cfg.MCP) {
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
