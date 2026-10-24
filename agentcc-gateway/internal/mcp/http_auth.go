package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// authenticateHTTP is the one auth chokepoint of the MCP HTTP surface. Every
// MCP HTTP handler calls it first, before it reads the body or the session:
// POST, GET and DELETE on the MCP endpoint. A handler that does not call it
// is a bug.
//
// When per-key auth is set, which the gateway does whenever request auth is
// on, every request must carry a valid key. One with no key is refused like
// one with a bad key, never served unfiltered: HTTP 401 with a Bearer
// challenge (RFC 7235, and the MCP authorization spec), no session created and
// nothing parsed. The key is read the way the gateway's KeyAuth reads it, so
// the allow and deny lists applied are those of the key the gateway checked.
//
// The principal rides in the returned request's context: its tool allow and
// deny lists, and the key's identity, which owns a session the request opens
// and is the only owner whose session it may use. CallContext.Request returns
// that request to every hook.
//
// With per-key auth unset (gateway request auth off) the request passes
// unchanged.
func (s *Server) authenticateHTTP(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	if s.keyAuth == nil {
		return r, true
	}
	rawKey := requestAPIKey(r)
	if rawKey == "" {
		writeUnauthorized(w, "missing API key")
		return nil, false
	}
	grant, valid := s.keyAuth.AuthenticateKey(rawKey)
	if !valid {
		writeUnauthorized(w, "invalid or inactive API key")
		return nil, false
	}
	ctx := context.WithValue(r.Context(), ctxKeySessionOwner, SessionOwner{KeyID: grant.KeyID, OrgID: grant.OrgID})
	if len(grant.AllowedTools) > 0 {
		ctx = context.WithValue(ctx, ctxKeyAllowedTools, grant.AllowedTools)
	}
	if len(grant.DeniedTools) > 0 {
		ctx = context.WithValue(ctx, ctxKeyDeniedTools, grant.DeniedTools)
	}
	return r.WithContext(ctx), true
}

// sessionOwner is the key authenticateHTTP found on the request: the zero
// owner when per-key auth is off (single tenant, every session shared).
func sessionOwner(ctx context.Context) SessionOwner {
	owner, _ := ctx.Value(ctxKeySessionOwner).(SessionOwner)
	return owner
}

// writeUnauthorized answers a request without a valid key. The request has
// not been parsed, so the JSON-RPC error carries a null id.
func writeUnauthorized(w http.ResponseWriter, message string) {
	w.Header().Set("WWW-Authenticate", models.BearerChallenge)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusUnauthorized)
	json.NewEncoder(w).Encode(NewErrorResponse(json.RawMessage("null"), ErrCodeInvalidRequest, message))
}

// requestAPIKey returns the request's API key by the rule KeyAuth applies in
// front of this endpoint: the bearer token of the Authorization header when
// present, else x-api-key. The allow and deny lists applied are therefore
// always those of the key the gateway checked.
func requestAPIKey(r *http.Request) string {
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		return strings.TrimPrefix(auth, "Bearer ")
	}
	return r.Header.Get("x-api-key")
}
