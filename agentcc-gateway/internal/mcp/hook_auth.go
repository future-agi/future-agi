package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// hookResolvePrincipal is step 1 of every chain (chain.go).
const hookResolvePrincipal = "resolvePrincipal"

// resolvePrincipal copies the caller authenticateHTTP resolved (a bearer
// Deps.BearerVerifier accepted, else a virtual key) into cc.Principal. On a
// server with an identity, a call whose request never passed authenticateHTTP
// is refused with HTTP 401, so a handler that skips the chokepoint cannot
// reach a tool. An anonymous-dev request, and any request on a server with no
// identity installed, runs with no principal.
//
// The one exception is the admin playground (POST /-/mcp/test, M9's
// AdminTestTool, M9 IR-7): its route checked the admin token, and it runs the
// chain on a copy of the admin request with no credential as principal
// admin:<org>, which it sets before the chain runs. An HTTP request on the MCP
// endpoint always carries what authenticateHTTP resolved, so no MCP caller can
// reach this case.
//
// Owner: M8. Refusal: HTTP 401 (missing_credentials).
// Keep observeHook as its first statement.
func resolvePrincipal(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolvePrincipal)
	caller, ok := callerAuthFrom(cc.Context())
	switch {
	case !ok && s.keyAuth == nil:
		return nil
	case !ok && cc.Principal.Kind == PrincipalAdmin:
		return nil
	case !ok:
		return s.unauthorized(cc.Request(), AuthMissingCredentials, "the request was not authenticated")
	case caller.anonymous:
		return nil
	}
	p := caller.principal
	p.Scopes = append([]string(nil), p.Scopes...)
	p.AllowedTools = append([]string(nil), p.AllowedTools...)
	cc.Principal = p
	return s.checkTokenScope(cc)
}

// Token scopes (MCP-PLAN M23): an access token grants the method families
// its scope names, and nothing else.
const (
	scopeTools     = "mcp:tools"
	scopeResources = "mcp:resources"
	scopePrompts   = "mcp:prompts"

	// AuthInsufficientScope is the data.code of a 403 for a method the
	// token's scope does not grant.
	AuthInsufficientScope = "insufficient_scope"
)

// methodScope is the scope a chained method needs: tools/* need mcp:tools,
// resources/* mcp:resources and prompts/* mcp:prompts.
func methodScope(method string) string {
	switch {
	case strings.HasPrefix(method, "tools/"):
		return scopeTools
	case strings.HasPrefix(method, "resources/"):
		return scopeResources
	case strings.HasPrefix(method, "prompts/"):
		return scopePrompts
	}
	return ""
}

// checkTokenScope refuses a call by an OAuth access token whose scope does
// not grant the call's method: HTTP 403 with the Bearer challenge
// error="insufficient_scope", the scope needed and the resource's metadata
// (RFC 6750 section 3.1, RFC 9728 section 5.1). Access tokens are the only
// principals whose claims carry a scope (mcp-access-token-claims-v1): a key
// has none, and a callback token is bounded by its allowed_tools instead
// (M10, M34), so neither is checked here.
func (s *Server) checkTokenScope(cc *CallContext) *Denial {
	p := cc.Principal
	if p.Kind != PrincipalOAuth {
		return nil
	}
	need := methodScope(cc.Method)
	if need == "" || slices.Contains(p.Scopes, need) {
		return nil
	}
	challenge := models.BearerChallenge + `, error="insufficient_scope", scope="` + need + `"`
	if prm := s.resourceMetadataURL(cc.Request()); prm != "" {
		challenge += `, resource_metadata="` + strings.ReplaceAll(prm, `"`, "%22") + `"`
	}
	h := http.Header{}
	h.Set("WWW-Authenticate", challenge)
	data, _ := json.Marshal(map[string]string{"code": AuthInsufficientScope, "scope": need})
	return &Denial{
		Type: models.ErrTypePermission, Code: AuthInsufficientScope,
		Message: "the access token's scope does not include " + need,
		RPCCode: ErrCodeUnauthorized, HTTPStatus: http.StatusForbidden, Header: h, Data: data,
	}
}

// errJWTRejected is the default verifier's answer to a JWT: no issuer is
// configured until M23 installs one.
var errJWTRejected = errors.New("mcp: bearer tokens are not accepted on this gateway")

// rejectJWTs is the default BearerVerifier: it rejects every JWT-shaped
// token and leaves anything else to key authentication.
type rejectJWTs struct{}

func (rejectJWTs) Verify(_ context.Context, token, _ string) (*Principal, error) {
	if strings.Count(token, ".") == 2 && strings.HasPrefix(token, "eyJ") {
		return nil, errJWTRejected
	}
	return nil, nil
}
