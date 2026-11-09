package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// registerMCPRevisionsRoutes registers GET /-/mcp/revisions?org_id= (M20): the
// tools of the org's own MCP servers that pinning holds or warns about, each
// an mcp-revision-v1 record under "revisions", from the org's pool only. Like
// every /-/mcp/* admin call it needs the admin token; a missing org_id is 400
// and an unknown org 404. Django pulls it to show an admin what changed and
// to write the pins an admin accepts (M21). Owner: M20.
func (s *Server) registerMCPRevisionsRoutes(router *Router) {
	m := s.mcp
	if m == nil {
		return
	}
	router.Handle("GET", "/-/mcp/revisions", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		orgID := r.URL.Query().Get("org_id")
		if orgID == "" {
			models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
			return
		}
		revisions, err := m.AdminRevisions(orgID)
		if err != nil {
			writeMCPAdmin(w, nil, err)
			return
		}
		writeMCPAdmin(w, map[string]any{"revisions": revisions}, nil)
	})
}
