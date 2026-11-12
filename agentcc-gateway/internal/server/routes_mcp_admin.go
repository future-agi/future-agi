package server

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// mcpAdminMaxBody caps POST /-/mcp/test's body.
const mcpAdminMaxBody = 1 << 20

// registerMCPAdminRoutes registers the org-scoped MCP admin API: GET /-/mcp/status, tools, resources and prompts, and POST
// /-/mcp/test. Every call names its org (org_id: a query parameter on the
// GETs, a body field on the POST) and is answered from that org's pool only;
// a missing org_id is 400 and an unknown one 404. They replace the five
// routes the route setup registers for the server's global registry (a
// later registration of a path wins), and POST /-/reload also rebuilds the
// static pool.
func (s *Server) registerMCPAdminRoutes(router *Router) {
	m := s.mcp
	if m == nil {
		return
	}
	get := func(path string, answer func(r *http.Request, orgID string) (any, error)) {
		router.Handle("GET", path, func(w http.ResponseWriter, r *http.Request) {
			if !s.checkAdminAuth(w, r) {
				return
			}
			orgID := r.URL.Query().Get("org_id")
			if orgID == "" {
				models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
				return
			}
			body, err := answer(r, orgID)
			writeMCPAdmin(w, body, err)
		})
	}
	get("/-/mcp/status", func(_ *http.Request, org string) (any, error) { return m.AdminStatus(org) })
	get("/-/mcp/tools", func(r *http.Request, org string) (any, error) {
		return m.AdminTools(org, r.URL.Query().Get("vs"))
	})
	get("/-/mcp/served-tools", func(r *http.Request, org string) (any, error) {
		return m.AdminServedServerTools(r.Context(), org, r.URL.Query().Get("vs"), r.URL.Query().Get("key_id"), r.URL.Query().Get("server_id"))
	})
	get("/-/mcp/resources", func(_ *http.Request, org string) (any, error) {
		items, err := m.AdminResources(org)
		return map[string]any{"resources": items}, err
	})
	get("/-/mcp/prompts", func(_ *http.Request, org string) (any, error) {
		items, err := m.AdminPrompts(org)
		return map[string]any{"prompts": items}, err
	})
	router.Handle("POST", "/-/mcp/test", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		var req struct {
			OrgID     string          `json:"org_id"`
			VS        string          `json:"vs"`
			Name      string          `json:"name"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, mcpAdminMaxBody)).Decode(&req); err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_json", "invalid request body"))
			return
		}
		switch {
		case req.OrgID == "":
			models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
			return
		case req.Name == "":
			models.WriteError(w, models.ErrBadRequest("missing_name", "tool name is required"))
			return
		case len(req.Arguments) > 0 && string(req.Arguments) != "null" && !strings.HasPrefix(strings.TrimSpace(string(req.Arguments)), "{"):
			models.WriteError(w, models.ErrBadRequest("invalid_arguments", "arguments must be an object"))
			return
		}
		res, err := m.AdminTestTool(r, req.OrgID, req.VS, req.Name, req.Arguments)
		writeMCPAdmin(w, res, err)
	})

	// /-/reload rebuilds the static YAML pool too: this replaces the core
	// route with one that runs the core reload, then, when it succeeded,
	// tells the MCP registry the reloaded mcp.static.org_id, as the wiring
	// does at startup (it decides whether a caller with no key or no org gets
	// the operator's servers), and hands the reloaded mcp config to the
	// tenancy (only the YAML servers whose upstream changed reconnect). The
	// registry learns the org id first, so a reload that binds the operator's
	// servers to an org stops serving them to a keyless caller before the
	// pools change.
	router.Handle("POST", "/-/reload", func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		s.reloadHandler(rec, r)
		if rec.status != http.StatusOK {
			return
		}
		m.Registry().SetStaticOrg(s.cfg.MCP.Static.OrgID)
		if t := m.Tenancy(); t != nil {
			t.ReloadStatic(s.cfg.MCP)
		}
	})
}

// statusRecorder passes a response through and keeps its status.
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

// writeMCPAdmin answers an admin call: the body, or the error's status.
func writeMCPAdmin(w http.ResponseWriter, body any, err error) {
	switch {
	case err == nil:
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(body)
	case errors.Is(err, mcp.ErrUnknownOrg):
		models.WriteError(w, models.ErrNotFound("org_not_found", "no MCP configuration for this org_id"))
	case errors.Is(err, mcp.ErrUnknownVirtualServer):
		models.WriteError(w, models.ErrNotFound("virtual_server_not_found", "virtual server not found"))
	case errors.Is(err, mcp.ErrUnknownServer):
		models.WriteError(w, models.ErrNotFound("server_not_found", "the org has no such MCP server"))
	case errors.Is(err, mcp.ErrUnknownKey):
		models.WriteError(w, models.ErrNotFound("key_not_found", "the org has no such active key"))
	case errors.Is(err, mcp.ErrPolicyUnavailable):
		e := models.ErrServiceUnavailable("the org's MCP policy is unavailable")
		e.Code = mcp.CodePolicyUnavailable
		models.WriteError(w, e)
	default:
		e := models.ErrServiceUnavailable("MCP tenancy is unavailable")
		e.Code = "mcp_tenancy_unavailable"
		models.WriteError(w, e)
	}
}
