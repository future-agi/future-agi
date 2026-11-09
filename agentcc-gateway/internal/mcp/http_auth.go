package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// HeaderRequestID is the response header naming the gateway's id for one MCP
// request.
const HeaderRequestID = "x-agentcc-request-id"

// authenticateHTTP is the one auth chokepoint of the MCP HTTP surface. Every
// MCP HTTP handler calls it first, before it reads the body or the session:
// POST, GET and DELETE on the endpoint today, and the legacy HTTP+SSE routes
// and any later MCP route when their lanes fill them (M12, M12x). A handler
// that does not call it is a bug.
//
// It names the request (request_id "mcp_" + ULID, in the context and in
// x-agentcc-request-id), then resolves the caller (principal.go): a bearer
// Deps.BearerVerifier accepts, else a virtual key with its status, expiry and
// address rules applied. A request with neither is refused like one with a
// bad credential, never served unfiltered: HTTP 401 with the gateway's Bearer
// challenge (RFC 7235, and the MCP authorization spec) and a JSON-RPC error
// -32001 whose data.code says why, with no session created and nothing
// parsed. The one exception is the identity's servesKeyless (request auth
// off, as on the release line, or mcp.allow_anonymous_dev). The key is read
// the way the gateway's KeyAuth reads it, so the allow and deny lists applied
// are those of the key the gateway checked.
//
// The resolved caller rides in the returned request's context, with the key's
// tool allow and deny lists; CallContext.Request returns that request to
// every hook, and hook 1 copies the principal into CallContext.Principal. A
// legacy session is bound to that principal (M12's sessionBindingOf), which
// supersedes round 4's per-key session owner.
//
// The gateway installs its identity on the server whatever auth.enabled says
// (wiring_mcp_identity.go), so a credential that is sent is always checked. A
// request with no credential is served with no principal in two cases only:
// with request auth off, the operator's servers while they are bound to no
// org and no virtual server is addressed, as the release line served them
// (MCP-APPS-PLAN ruling 7); and under mcp.allow_anonymous_dev (tests only).
// A server with no identity and no key authenticator (one a unit test builds)
// serves every request without a principal.
func (s *Server) authenticateHTTP(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	id := "mcp_" + ulid.Make().String()
	w.Header().Set(HeaderRequestID, id)
	r = r.WithContext(models.WithRequestID(r.Context(), id))
	if s.keyAuth == nil {
		return r.WithContext(withCallerAuth(r.Context(), &callerAuth{anonymous: true})), true
	}
	caller, fail := s.resolveCaller(r)
	if fail != nil {
		writeRPCDenial(w, json.RawMessage("null"), s.unauthorized(r, fail.code, fail.message))
		return nil, false
	}
	// A request the gateway's key gate handed here without a gateway key (MCP
	// sign-in on) meets its caller's organization's address rules now that
	// the organization is known, exactly as that organization's keys meet
	// them at the gate.
	if check, ok := middleware.OrgAddressCheck(r.Context()); ok && !caller.anonymous && caller.principal.OrgID != "" {
		if err := check(caller.principal.OrgID); err != nil {
			models.WriteError(w, err)
			return nil, false
		}
	}
	ctx := withCallerAuth(r.Context(), caller)
	ctx = withToolLists(ctx, caller.allowedTools, caller.deniedTools)
	return r.WithContext(ctx), true
}

// withToolLists puts a key's tool allow and deny lists where the access hooks
// read them (filterToolsByKey, checkToolKeyAccess).
func withToolLists(ctx context.Context, allowed, denied []string) context.Context {
	if len(allowed) > 0 {
		ctx = context.WithValue(ctx, ctxKeyAllowedTools, allowed)
	}
	if len(denied) > 0 {
		ctx = context.WithValue(ctx, ctxKeyDeniedTools, denied)
	}
	return ctx
}

// requestAPIKey returns the API key a request carries, by the rule the
// gateway's KeyAuth uses in front of this endpoint: the token of an
// "Authorization: Bearer" header, otherwise the x-api-key header. A different
// rule here would filter tools by a key other than the one the gateway checked,
// or by none (x-api-key used to be ignored, which skipped the key's allow-list).
func requestAPIKey(r *http.Request) string {
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		return strings.TrimPrefix(auth, "Bearer ")
	}
	return r.Header.Get("x-api-key")
}
