package mcpmock

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"net/http"
	"strings"
	"sync"
	"time"
)

// OAuth lifetimes. Access tokens live 15 minutes (decision 37's ceiling).
const (
	accessTokenTTL = 15 * time.Minute
	authCodeTTL    = 10 * time.Minute
	defaultSubject = "mcpmock-user"
)

type authCode struct {
	clientID, redirectURI, challenge, resource, scope, sub string
	exp                                                    time.Time
}

type accessToken struct {
	aud, clientID, scope, sub, family string
	exp                               time.Time
}

type refreshToken struct {
	clientID, resource, scope, sub, family string
	used                                   bool
}

// oauthServer is the mock's own authorization server (RFC 8414) for --auth
// oauth: PKCE S256 only, iss on the redirect (RFC 9207), the RFC 8707
// resource checked on both legs, and refresh tokens that rotate (a reused one
// revokes its whole family). Tokens are opaque and live in memory.
type oauthServer struct {
	mu          sync.Mutex
	codes       map[string]*authCode
	access      map[string]accessToken
	refresh     map[string]*refreshToken
	revoked     map[string]bool // refresh families
	now         func() time.Time
	clients     map[string]registration
	revocations []revocation
	ttl         time.Duration
}

func newOAuthServer() *oauthServer {
	return &oauthServer{codes: map[string]*authCode{}, access: map[string]accessToken{},
		refresh: map[string]*refreshToken{}, revoked: map[string]bool{}, now: time.Now, clients: map[string]registration{}, ttl: accessTokenTTL}
}

func (o *oauthServer) mintAccess(aud, clientID, scope, sub string, ttl time.Duration) string {
	tok := "mcpmock-at-" + randomID()
	o.mu.Lock()
	o.access[tok] = accessToken{aud: aud, clientID: clientID, scope: scope, sub: sub, exp: o.now().Add(ttl)}
	o.mu.Unlock()
	return tok
}

func (o *oauthServer) mintRefresh(clientID, resource, scope, sub, family string) string {
	tok := "mcpmock-rt-" + randomID()
	if family == "" {
		family = randomID()
	}
	o.mu.Lock()
	o.refresh[tok] = &refreshToken{clientID: clientID, resource: resource, scope: scope, sub: sub, family: family}
	o.mu.Unlock()
	return tok
}

// check validates a bearer for resource: known, unexpired and minted for that
// audience. It returns the token's subject.
func (o *oauthServer) check(token, resource string) (string, string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	t, ok := o.access[token]
	switch {
	case !ok:
		return "", "unknown token"
	case o.revoked[t.family]:
		return "", "the token family is revoked"
	case !o.now().Before(t.exp):
		return "", "the token has expired"
	case t.aud != resource:
		return "", "the token's audience is not this server"
	}
	return t.sub, ""
}

// authorize gates the MCP endpoints by --auth. On refusal it has written the
// 401, with a WWW-Authenticate that names the PRM in OAuth mode.
func (m *Mock) authorize(w http.ResponseWriter, r *http.Request) (string, bool) {
	got := bearerToken(r)
	switch {
	case m.cfg.Auth == "none":
		return "", true
	case strings.HasPrefix(m.cfg.Auth, "bearer:"):
		want := strings.TrimPrefix(m.cfg.Auth, "bearer:")
		if got != "" && subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1 {
			return "", true
		}
		w.Header().Set("WWW-Authenticate", `Bearer realm="mcpmock"`)
		_ = writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid_token"})
		return "", false
	}
	challenge := `Bearer resource_metadata="` + m.prmURL() + `"`
	if got == "" {
		w.Header().Set("WWW-Authenticate", challenge)
		_ = writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid_request", "error_description": "a bearer token is required"})
		return "", false
	}
	sub, why := m.oauth.check(got, m.resourceURI())
	if why != "" {
		w.Header().Set("WWW-Authenticate", challenge+`, error="invalid_token"`)
		_ = writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid_token", "error_description": why})
		return "", false
	}
	return sub, true
}

func bearerToken(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if len(h) > 7 && strings.EqualFold(h[:7], "bearer ") {
		return strings.TrimSpace(h[7:])
	}
	return ""
}

func (m *Mock) prmURL() string {
	return m.cfg.PublicURL + "/.well-known/oauth-protected-resource" + m.cfg.Path
}

// registerOAuth mounts the PRM (RFC 9728) at the root and with the endpoint
// path appended, the AS metadata (RFC 8414), /authorize and /token.
func (m *Mock) registerOAuth(mux *http.ServeMux) {
	prm := func(w http.ResponseWriter, _ *http.Request) {
		_ = writeJSON(w, http.StatusOK, map[string]any{
			"resource":                 m.resourceURI(),
			"authorization_servers":    []string{m.cfg.PublicURL},
			"bearer_methods_supported": []string{"header"},
			"scopes_supported":         []string{"mcp"},
		})
	}
	mux.HandleFunc("GET /.well-known/oauth-protected-resource", prm)
	mux.HandleFunc("GET /.well-known/oauth-protected-resource"+m.cfg.Path, prm)
	mux.HandleFunc("GET /.well-known/oauth-authorization-server", func(w http.ResponseWriter, _ *http.Request) {
		_ = writeJSON(w, http.StatusOK, map[string]any{
			"issuer":                                         m.cfg.PublicURL,
			"authorization_endpoint":                         m.cfg.PublicURL + "/authorize",
			"token_endpoint":                                 m.cfg.PublicURL + "/token",
			"registration_endpoint":                          m.cfg.PublicURL + "/register",
			"revocation_endpoint":                            m.cfg.PublicURL + "/revoke",
			"response_types_supported":                       []string{"code"},
			"grant_types_supported":                          []string{"authorization_code", "refresh_token"},
			"code_challenge_methods_supported":               []string{"S256"},
			"token_endpoint_auth_methods_supported":          []string{"none", "client_secret_post", "client_secret_basic"},
			"scopes_supported":                               []string{"mcp"},
			"authorization_response_iss_parameter_supported": true,
		})
	})
	mux.HandleFunc("GET /authorize", m.serveAuthorize)
	mux.HandleFunc("POST /authorize", m.serveAuthorize)
	mux.HandleFunc("POST /token", m.serveToken)
	mux.HandleFunc("POST /register", m.serveRegister)
	mux.HandleFunc("POST /revoke", m.serveRevoke)
	mux.HandleFunc("GET /__control/oauth/received", m.oauthReceipts)
	mux.HandleFunc("POST /__control/oauth/lifetime", m.oauthLifetime)
}

// pkceMatches checks an RFC 7636 S256 verifier against its challenge.
func pkceMatches(verifier, challenge string) bool {
	if verifier == "" {
		return false
	}
	sum := sha256.Sum256([]byte(verifier))
	got := base64.RawURLEncoding.EncodeToString(sum[:])
	return subtle.ConstantTimeCompare([]byte(got), []byte(challenge)) == 1
}

func tokenError(w http.ResponseWriter, code, desc string) {
	_ = writeJSON(w, http.StatusBadRequest, map[string]any{"error": code, "error_description": desc})
}

// serveToken is the token endpoint: authorization_code (PKCE verifier,
// client, redirect and resource must match the code) and refresh_token
// (rotation; a reused refresh token revokes its family).
func (m *Mock) serveToken(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		tokenError(w, "invalid_request", "the body must be a form")
		return
	}
	f := r.PostForm
	clientID, authenticated, viaHeader := m.clientAuthenticated(r)
	if !authenticated {
		clientError(w, viaHeader)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	grant := f.Get("grant_type")
	if grant != "authorization_code" && grant != "refresh_token" {
		tokenError(w, "unsupported_grant_type", "want authorization_code or refresh_token")
		return
	}
	if !m.clientGrantAllowed(clientID, grant) {
		tokenError(w, "unauthorized_client", "client did not register this grant type")
		return
	}
	switch grant {
	case "authorization_code":
		m.redeemCode(w, f.Get("code"), clientID, f.Get("redirect_uri"), f.Get("code_verifier"), f.Get("resource"))
	case "refresh_token":
		m.redeemRefresh(w, f.Get("refresh_token"), clientID, f.Get("resource"))
	}
}

func (m *Mock) redeemCode(w http.ResponseWriter, code, clientID, redirectURI, verifier, resource string) {
	o := m.oauth
	o.mu.Lock()
	g, ok := o.codes[code]
	delete(o.codes, code) // single use, even when the redemption fails
	o.mu.Unlock()
	switch {
	case !ok || o.now().After(g.exp):
		tokenError(w, "invalid_grant", "unknown, used or expired code")
	case g.clientID != clientID || g.redirectURI != redirectURI:
		tokenError(w, "invalid_grant", "client_id or redirect_uri does not match the authorization request")
	case !pkceMatches(verifier, g.challenge):
		tokenError(w, "invalid_grant", "the PKCE code_verifier does not match")
	case resource != "" && resource != g.resource:
		tokenError(w, "invalid_target", "resource does not match the authorization request")
	default:
		m.issueTokens(w, clientID, g.resource, g.scope, g.sub, "")
	}
}

func (m *Mock) redeemRefresh(w http.ResponseWriter, tok, clientID, resource string) {
	o := m.oauth
	o.mu.Lock()
	rt, ok := o.refresh[tok]
	reused := ok && rt.used && rt.clientID == clientID
	if reused {
		o.revoked[rt.family] = true
	}
	valid := ok && !rt.used && !o.revoked[rt.family] && rt.clientID == clientID
	// A wrong resource is refused before the token is used up, so one bad
	// request does not turn the client's correct retry into a reuse.
	wrongTarget := valid && resource != "" && resource != rt.resource
	if valid && !wrongTarget {
		rt.used = true
	}
	o.mu.Unlock()
	switch {
	case reused:
		tokenError(w, "invalid_grant", "refresh token reused: its family is revoked")
	case !valid:
		tokenError(w, "invalid_grant", "unknown, revoked or foreign refresh token")
	case wrongTarget:
		tokenError(w, "invalid_target", "resource does not match the grant")
	default:
		m.issueTokens(w, clientID, rt.resource, rt.scope, rt.sub, rt.family)
	}
}

func (m *Mock) issueTokens(w http.ResponseWriter, clientID, resource, scope, sub, family string) {
	if family == "" {
		family = randomID()
	}
	m.oauth.mu.Lock()
	ttl := m.oauth.ttl
	m.oauth.mu.Unlock()
	access := m.oauth.mintAccess(resource, clientID, scope, sub, ttl)
	m.oauth.mu.Lock()
	at := m.oauth.access[access]
	at.family = family
	m.oauth.access[access] = at
	m.oauth.mu.Unlock()
	out := map[string]any{
		"access_token": access,
		"token_type":   "Bearer",
		"expires_in":   int(ttl.Seconds()),
		"scope":        scope,
	}
	if m.clientGrantAllowed(clientID, "refresh_token") {
		out["refresh_token"] = m.oauth.mintRefresh(clientID, resource, scope, sub, family)
	}
	_ = writeJSON(w, http.StatusOK, out)
}
