package mcp

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
)

// Caller identity on the MCP surface (M8; plan section 6.16, Principal, and
// section 6.18's protocol-level answers). authenticateHTTP resolves who is
// calling on every method before anything is parsed: a bearer that
// Deps.BearerVerifier accepts, else a virtual key with its status, expiry and
// address rules applied, else HTTP 401. Hook 1 copies the result into
// CallContext.Principal.

// ErrCodeUnauthorized is the JSON-RPC error code of an MCP 401.
const ErrCodeUnauthorized = -32001

// The data.code of an MCP 401 (plan section 6.18, protocol-level answers).
const (
	AuthMissingCredentials = "missing_credentials"
	AuthInvalidAPIKey      = "invalid_api_key"
	AuthKeyExpired         = "key_expired"
	AuthIPNotAllowed       = "ip_not_allowed"
	AuthInvalidToken       = "invalid_token"
)

// Identity is how the gateway authenticates MCP callers. The gateway installs
// one on its MCP server whatever auth.enabled says (wiring_mcp_identity.go),
// so a credential that is sent is checked on every gateway, and a caller with
// no credential is refused wherever it would need one.
type Identity struct {
	// Keys holds the virtual keys. With none, no key is valid.
	Keys *auth.KeyStore
	// AllowAnonymousDev is mcp.allow_anonymous_dev: a request that sends no
	// credential is served with no principal. A credential that is sent is
	// still checked. Tests only.
	AllowAnonymousDev bool
	// KeylessStatic is gateway request auth off (auth.enabled false): a
	// request that sends no credential is served the operator's servers with
	// no principal, as the release line served it (MCP-APPS-PLAN ruling 7),
	// while those servers are bound to no org (mcp.static.org_id unset) and
	// the request addresses no virtual server. Anywhere else it is 401: a
	// virtual server and an org's servers need an identity. A credential that
	// is sent is still checked.
	KeylessStatic bool
}

// SetIdentity installs id as the server's caller authentication, replacing
// any KeyAuthenticator SetKeyAuth attached. Call it before the server serves.
func (s *Server) SetIdentity(id Identity) {
	s.SetKeyAuth(&identityAuth{keys: id.Keys, anonymousDev: id.AllowAnonymousDev, keylessStatic: id.KeylessStatic})
}

// identityAuth is the installed Identity. It rides in the server's key
// authenticator slot, so a server with no identity installed is the only one
// that serves every request without a principal (a bare server, as unit
// tests build).
type identityAuth struct {
	keys          *auth.KeyStore
	anonymousDev  bool
	keylessStatic bool
}

// AuthenticateKey keeps identityAuth a KeyAuthenticator: the key's identity
// (read under the key store's lock) and its tool lists. With no request
// there is no caller address, so a key bound to addresses is refused.
func (ia *identityAuth) AuthenticateKey(rawKey string) (KeyGrant, bool) {
	key, fail := ia.resolveKey(nil, rawKey)
	if fail != nil {
		return KeyGrant{}, false
	}
	p := ia.keyPrincipal(key)
	allowed, denied := key.MCPToolLists()
	return KeyGrant{KeyID: p.KeyID, OrgID: p.OrgID, AllowedTools: allowed, DeniedTools: denied}, true
}

// resolveKey returns the active key rawKey names, or why it is refused. r is
// the request whose address an address-bound key is checked against.
func (ia *identityAuth) resolveKey(r *http.Request, rawKey string) (*auth.APIKey, *authFailure) {
	if ia.keys == nil {
		return nil, &authFailure{AuthInvalidAPIKey, "invalid API key"}
	}
	key := ia.keys.Lookup(rawKey)
	switch {
	case key == nil:
		return nil, &authFailure{AuthInvalidAPIKey, "invalid API key"}
	case key.Status == "revoked":
		return nil, &authFailure{AuthInvalidAPIKey, "API key has been revoked"}
	case key.Status == "expired" || key.IsExpired():
		return nil, &authFailure{AuthKeyExpired, "API key has expired"}
	case !key.IsActive():
		return nil, &authFailure{AuthInvalidAPIKey, "API key is not active"}
	}
	if len(key.AllowedIPs) > 0 {
		var ip net.IP
		if r != nil {
			ip = net.ParseIP(clientIP(r))
		}
		if ip == nil || !ipacl.CheckIP(ip, key.AllowedIPs) {
			return nil, &authFailure{AuthIPNotAllowed, "API key is not authorized from this IP address"}
		}
	}
	// Authenticate records the use, and is nil if the key stopped being
	// active since the lookup.
	if ia.keys.Authenticate(rawKey) == nil {
		return nil, &authFailure{AuthInvalidAPIKey, "API key is not active"}
	}
	return key, nil
}

// keyPrincipal copies the authenticated key's id, org and person (733/IR-2).
// UserID comes only from reserved user_id written by control-plane sync or
// verified delegated issuance, in the canonical UUID form Django emits.
// KeyStore.Identity enforces provenance and syntax under its lock: Owner,
// caller metadata, config seeds and request headers never identify a person,
// and concurrent Update cannot race the metadata read. Missing or malformed
// user_id leaves UserID empty; it must not select another person's grant.
//
// Django lists only live-membership owners. resolveKey refuses a key once a
// revoke or authoritative sync retires/drops it, before any principal exists.
// Delegated issuance is in-process, not an additional MCP bearer entry point.
// OWED on the first 708j head carrying both lines: grant-isolation with the
// real per-user credential resolver (auth_required/no_user_identity for an
// empty UserID), plus W08 steps 3/7. That resolver is absent on this line.
func (ia *identityAuth) keyPrincipal(key *auth.APIKey) Principal {
	id := ia.keys.Identity(key)
	return Principal{Kind: PrincipalKey, KeyID: id.KeyID, OrgID: id.OrgID, UserID: id.UserID}
}

// ClaimsVerifier is a BearerVerifier whose tokens carry agent claims (runtime
// tokens; M23 installs it, G33 issues them). authenticateHTTP asks it instead
// of Verify, and hook 2 applies the claims over the agent headers (decision
// 23).
type ClaimsVerifier interface {
	VerifyClaims(ctx context.Context, token, audience string) (*Principal, *models.RuntimeIdentity, error)
}

// callerAuth is what authenticateHTTP resolved for one request.
type callerAuth struct {
	principal Principal
	// runtime holds a runtime token's claims, nil for any other caller.
	runtime *models.RuntimeIdentity
	// anonymous is a request served with no principal: anonymous dev, or a
	// server with no identity installed.
	anonymous    bool
	allowedTools []string
	deniedTools  []string
}

type callerAuthKey struct{}

func withCallerAuth(ctx context.Context, c *callerAuth) context.Context {
	return context.WithValue(ctx, callerAuthKey{}, c)
}

// callerAuthFrom returns what authenticateHTTP resolved for the request ctx
// belongs to, or false when the request never passed it.
func callerAuthFrom(ctx context.Context) (*callerAuth, bool) {
	c, ok := ctx.Value(callerAuthKey{}).(*callerAuth)
	return c, ok && c != nil
}

// authFailure is why a caller is refused: a section 6.18 code and a message.
type authFailure struct {
	code    string
	message string
}

// resolveCaller resolves who is calling r, in the contract's order: a bearer
// the verifier accepts, then a virtual key, then nothing (401 unless the
// identity serves the request keyless: servesKeyless). s.keyAuth is set.
func (s *Server) resolveCaller(r *http.Request) (*callerAuth, *authFailure) {
	token := requestAPIKey(r)
	if bearer, ok := bearerToken(r); ok {
		c, handled, fail := s.verifyBearer(r, bearer)
		if fail != nil || handled {
			return c, fail
		}
	}
	ia, isIdentity := s.keyAuth.(*identityAuth)
	if token == "" {
		if isIdentity && s.servesKeyless(ia, r) {
			return &callerAuth{anonymous: true}, nil
		}
		return nil, &authFailure{AuthMissingCredentials, "missing credentials: send an API key as a Bearer token or in x-api-key"}
	}
	if !isIdentity {
		// A KeyAuthenticator attached with SetKeyAuth (request auth on, before
		// the wiring installs the identity, and tests): tool lists only.
		// The round-4 KeyGrant names the key and its org (merge note A).
		grant, valid := s.keyAuth.AuthenticateKey(token)
		if !valid {
			return nil, &authFailure{AuthInvalidAPIKey, "invalid or inactive API key"}
		}
		return &callerAuth{
			principal:    Principal{Kind: PrincipalKey, KeyID: grant.KeyID, OrgID: grant.OrgID},
			allowedTools: grant.AllowedTools, deniedTools: grant.DeniedTools,
		}, nil
	}
	key, fail := ia.resolveKey(r, token)
	if fail != nil {
		return nil, fail
	}
	// The key's MCP lists (M10 IR-1), never its /v1 function-tool lists
	// while an MCP list is set.
	allowed, denied := key.MCPToolLists()
	return &callerAuth{principal: ia.keyPrincipal(key), allowedTools: allowed, deniedTools: denied}, nil
}

// servesKeyless reports whether the identity ia serves r, a request with no
// credential, with no principal: always under mcp.allow_anonymous_dev, and
// with request auth off (KeylessStatic) when that serves it what the release
// line did, the operator's servers. Those are the legacy view a principal
// with no org gets (usesVirtualServers) only while mcp.static.org_id is
// unset, and never on a virtual server's address.
func (s *Server) servesKeyless(ia *identityAuth, r *http.Request) bool {
	if ia.anonymousDev {
		return true
	}
	return ia.keylessStatic && requestedVirtualServer(r) == "" && s.registry.StaticOrg() == ""
}

// verifyBearer asks Deps.BearerVerifier about a bearer token. handled is
// false when the verifier does not handle the token, which is then tried as
// a key; a token it refuses is 401 invalid_token, never a key.
func (s *Server) verifyBearer(r *http.Request, token string) (c *callerAuth, handled bool, fail *authFailure) {
	v := s.Deps().BearerVerifier
	if v == nil {
		return nil, false, nil
	}
	audience := r.URL.Path
	var p *Principal
	var rt *models.RuntimeIdentity
	var err error
	if cv, ok := v.(ClaimsVerifier); ok {
		p, rt, err = cv.VerifyClaims(r.Context(), token, audience)
	} else {
		p, err = v.Verify(r.Context(), token, audience)
	}
	switch {
	case err != nil:
		return nil, true, &authFailure{AuthInvalidToken, "invalid bearer token"}
	case p == nil:
		return nil, false, nil
	}
	cp := *p
	cp.Scopes = append([]string(nil), p.Scopes...)
	cp.AllowedTools = append([]string(nil), p.AllowedTools...)
	return &callerAuth{principal: cp, runtime: rt}, true, nil
}

// bearerToken is the token of an "Authorization: Bearer" header, the only
// place a verifier token is read from; x-api-key carries keys only.
func bearerToken(r *http.Request) (string, bool) {
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	return token, ok && token != ""
}

// clientIP is the caller's address by the rule every IP check in the gateway
// uses (round 6 of the W3 security port): middleware.ClientIP, the address
// TrustedClientIP resolved for the request from server.trusted_proxies or
// server.trusted_hops, else the TCP peer. X-Forwarded-For and X-Real-IP count
// only when a proxy the gateway trusts wrote them, so a caller holding a key
// bound to addresses cannot name an allowed address in a header. KeyAuth and
// ip_acl on /v1 read the same address, so a key bound to addresses works
// behind the same proxy on both paths.
func clientIP(r *http.Request) string {
	return middleware.ClientIP(r)
}

// unauthorized is the 401 denial for code on request r: the gateway's Bearer
// challenge (with the resource_metadata of the resource r addresses once M23
// sets Deps.ResourceMetadataURL) and a JSON-RPC error -32001 whose data.code
// is code.
func (s *Server) unauthorized(r *http.Request, code, message string) *Denial {
	h := http.Header{}
	h.Set("WWW-Authenticate", bearerChallenge(s.resourceMetadataURL(r)))
	data, _ := json.Marshal(map[string]string{"code": code})
	return &Denial{
		Type: models.ErrTypeAuthentication, Code: code, Message: message,
		RPCCode: ErrCodeUnauthorized, HTTPStatus: http.StatusUnauthorized, Header: h, Data: data,
	}
}

// resourceMetadataURL is the protected-resource metadata address (RFC 9728)
// of the resource r addresses, "" while M23 has set none.
// Deps.ResourceMetadataURL is the endpoint's own; a virtual server is a
// resource of its own (decision 37), and its metadata lives at the endpoint's
// address plus /v/{vs_id}, the id escaped as oauthrs escapes it in the
// virtual server's address, so the metadata a client is sent to names the
// address it called (RFC 9728 section 3.3).
func (s *Server) resourceMetadataURL(r *http.Request) string {
	base := s.Deps().ResourceMetadataURL
	if base == "" {
		return ""
	}
	if vsID := requestedVirtualServer(r); vsID != "" {
		return base + "/v/" + url.PathEscape(vsID)
	}
	return base
}

// bearerChallenge is models.BearerChallenge, every gateway 401's challenge,
// with the protected-resource metadata URL when one is set (RFC 9728).
func bearerChallenge(resourceMetadataURL string) string {
	if resourceMetadataURL == "" {
		return models.BearerChallenge
	}
	return models.BearerChallenge + `, resource_metadata="` + strings.ReplaceAll(resourceMetadataURL, `"`, "%22") + `"`
}
