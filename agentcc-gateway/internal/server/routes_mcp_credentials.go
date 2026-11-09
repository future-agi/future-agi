package server

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// registerMCPCredentialsRoutes registers POST /-/mcp/credentials/purge (MC.7,
// admin token): Django calls it when a user disconnects an account or a
// credential is revoked, and the gateway drops the cached upstream tokens of
// that server in that org, of one user, or of every user and the org's
// shared account (no user_id; CN24), so the next call asks Django again.
// With revision (a shared static credential's grant revision, no user_id) it
// instead revokes the org's pushed grants of that server at or below it, on
// every replica, and leaves personal grants and newer pushed grants alone.
// Answer: {"purged": n}. Owner: M25.
func (s *Server) registerMCPCredentialsRoutes(router *Router) {
	router.Handle("POST", "/-/mcp/credentials/purge", func(w http.ResponseWriter, r *http.Request) {
		if !s.checkAdminAuth(w, r) {
			return
		}
		var req struct {
			OrgID    string `json:"org_id"`
			UserID   string `json:"user_id"`
			ServerID string `json:"server_id"`
			Revision int64  `json:"revision"`
		}
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, mcpAdminMaxBody)).Decode(&req); err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_json", "invalid request body"))
			return
		}
		switch {
		case req.OrgID == "":
			models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
			return
		case req.ServerID == "":
			models.WriteError(w, models.ErrBadRequest("missing_server_id", "server_id is required"))
			return
		case req.Revision < 0 || (req.Revision > 0 && req.UserID != ""):
			models.WriteError(w, models.ErrBadRequest("invalid_revision", "revision is a positive shared grant revision and names no user_id"))
			return
		}
		purged := 0
		if req.Revision > 0 {
			var revoker mcp.PushedGrantRevoker
			if s.mcp != nil {
				revoker, _ = s.mcp.Deps().CredentialResolver.(mcp.PushedGrantRevoker)
			}
			if revoker == nil {
				// A legacy purge cannot install a distributed revision fence.
				// Never let Django clear its durable obligation on that basis.
				models.WriteError(w, &models.APIError{
					Status: http.StatusNotImplemented, Type: models.ErrTypeServer,
					Code:    "revision_revocation_unsupported",
					Message: "this replica cannot revoke pushed grant revisions; retain the obligation and retry a capable replica",
				})
				return
			}
			err := revoker.RevokePushedGrants(r.Context(), req.OrgID, req.ServerID, req.Revision)
			if errors.Is(err, mcp.ErrInvalidCredentialKey) {
				models.WriteError(w, models.ErrBadRequest("invalid_id", "org_id or server_id is not a valid id"))
				return
			}
			if err != nil {
				slog.Warn("mcp pushed grant revocation failed", "org", req.OrgID, "server", req.ServerID, "error", err)
				models.WriteError(w, models.ErrServiceUnavailable("the pushed grant could not be revoked; retry"))
				return
			}
		} else if m := s.mcp; m != nil {
			if p, ok := m.Deps().CredentialResolver.(mcp.CredentialPurger); ok {
				n, err := p.PurgeCredentials(r.Context(), req.OrgID, req.UserID, req.ServerID)
				if errors.Is(err, mcp.ErrInvalidCredentialKey) {
					models.WriteError(w, models.ErrBadRequest("invalid_id", "org_id, user_id or server_id is not a valid id"))
					return
				}
				if err != nil {
					slog.Warn("mcp credential purge failed", "org", req.OrgID, "server", req.ServerID, "error", err)
					models.WriteError(w, models.ErrServiceUnavailable("the cached credentials could not be purged; retry"))
					return
				}
				purged = n
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(map[string]int{"purged": purged})
	})
}
