package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// prmMaxAge is how long a client may cache protected-resource metadata. It
// changes only with the deployment's configuration.
const prmMaxAge = "300"

// registerMCPOAuthRoutes registers, when mcp.oauth.enabled, the OAuth
// protected-resource metadata (RFC 9728) of the MCP endpoint and of each
// virtual server, GET /.well-known/oauth-protected-resource{endpoint} and
// .../v/{vs_id}, unauthenticated and cacheable; and PUT /-/mcp/oauth/keys
// (mcp-oauth-keys-push-v1, admin token), where the authorization server
// pushes its verification keys. With OAuth off none is registered and the
// MCP endpoint behaves exactly as before.
func (s *Server) registerMCPOAuthRoutes(router *Router) {
	if s.mcp == nil || !s.cfg.MCP.ClientOAuthEnabled || !s.cfg.MCP.OAuth.Enabled {
		return
	}
	res, err := oauthrs.NewResource(oauthrs.ConfigFrom(s.cfg.MCP))
	if err != nil {
		// The wiring refuses to start on the same error (wiring_mcp_oauth.go).
		slog.Error("mcp oauth: protected-resource metadata is not served", "error", err)
		return
	}
	prefix := oauthrs.WellKnownPrefix + res.Endpoint()
	router.Handle("GET", prefix, func(w http.ResponseWriter, r *http.Request) {
		writeResourceMetadata(w, res.Metadata(""))
	})
	if s.cfg.MCP.VirtualServersEnabled {
		router.Handle("GET", prefix+"/v/{vs_id}", func(w http.ResponseWriter, r *http.Request) {
			// The id is read from the path the router matched, never from the
			// query string the router copies it into.
			vsID, ok := strings.CutPrefix(r.URL.Path, prefix+"/v/")
			if !ok || vsID == "" || strings.Contains(vsID, "/") {
				models.WriteError(w, models.ErrNotFound("not_found", "Unknown endpoint: "+r.URL.Path))
				return
			}
			writeResourceMetadata(w, res.Metadata(vsID))
		})
	}
	router.Handle("PUT", "/-/mcp/oauth/keys", s.handleMCPOAuthKeysPush)
}

// writeResourceMetadata writes a metadata document with its cache headers.
func writeResourceMetadata(w http.ResponseWriter, doc oauthrs.Metadata) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age="+prmMaxAge)
	json.NewEncoder(w).Encode(doc)
}

// mcpOAuthKeys is the installed verifier's key push (oauthrs.Verifier).
type mcpOAuthKeys interface {
	PushKeys(ctx context.Context, body []byte) (oauthrs.PushResult, error)
}

// handleMCPOAuthKeysPush serves PUT /-/mcp/oauth/keys: the admin token, then
// the push, applied only when its version is newer. 400 is a push refused
// whole (nothing changed), 503 a push the shared store did not take (push
// again).
func (s *Server) handleMCPOAuthKeysPush(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	keys, ok := s.mcp.Deps().BearerVerifier.(mcpOAuthKeys)
	if !ok {
		models.WriteError(w, models.ErrServiceUnavailable("the MCP resource server is not running on this gateway"))
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, oauthrs.MaxPushBytes))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_keys_push", "the body is unreadable or larger than the push limit"))
		return
	}
	result, err := keys.PushKeys(r.Context(), body)
	switch {
	case errors.Is(err, oauthrs.ErrInvalidPush):
		models.WriteError(w, models.ErrBadRequest("invalid_keys_push", err.Error()))
		return
	case errors.Is(err, oauthrs.ErrStoreUnavailable):
		models.WriteError(w, models.ErrServiceUnavailable("the keys were not stored; push again"))
		return
	case err != nil:
		models.WriteError(w, models.ErrInternal("the key push failed"))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}
