package server

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// adminDecisionModels lets the authenticated backend list one session org's
// catalog without borrowing or minting a customer key. It shares the public
// /v1/decisions/models projection, not its node-provider fallback or key ACLs:
// the backend has already authorized the session's organization/workspace.
func (s *Server) adminDecisionModels(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	orgID := r.URL.Query().Get("org_id") // Bound from the route by Router.
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
		return
	}
	entries := []decisionModel{}
	if s.TenantStore != nil {
		if org := s.TenantStore.Get(orgID); org != nil {
			entries = s.handlers.orgDecisionModels(org)
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	writeDecisionModels(w, entries)
}
