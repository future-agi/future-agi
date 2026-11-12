package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The identity tests' virtual keys and org. Every value is a fake for tests.
const (
	m8Org         = "org-m8"
	m8KeyShared   = "m8-test-key-shared"
	m8KeyPersonal = "m8-test-key-personal"
	m8KeyRevoked  = "m8-test-key-revoked"
	m8KeyExpired  = "m8-test-key-expired"
	m8KeyIP       = "m8-test-key-ip-bound"
	m8KeyOther    = "m8-test-key-other-org"
	m8KeyTools    = "m8-test-key-one-tool"
)

// newM8KeyStore holds one key of each kind the principal rules tell apart: a
// shared org key, a key marked personal, a revoked one, an expired one, one bound to
// 10.0.0.0/8, a key of another org, and one allowed a single tool.
func newM8KeyStore(t *testing.T) *auth.KeyStore {
	t.Helper()
	org := map[string]string{"org_id": m8Org}
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "shared", Key: m8KeyShared, Owner: "user-shared-owner", Metadata: org},
		{Name: "personal", Key: m8KeyPersonal, Owner: "user-7", Metadata: map[string]string{"org_id": m8Org, "mcp_personal": "true"}},
		{Name: "revoked", Key: m8KeyRevoked, Metadata: org},
		{Name: "expired", Key: m8KeyExpired, Metadata: org, ExpiresAt: time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)},
		{Name: "ip", Key: m8KeyIP, Metadata: org, AllowedIPs: []string{"10.0.0.0/8"}},
		{Name: "other", Key: m8KeyOther, Metadata: map[string]string{"org_id": "org-other"}},
		{Name: "tools", Key: m8KeyTools, Metadata: org, AllowedTools: []string{"gh_t1"}},
	}})
	for _, k := range ks.List() {
		if k.Name == "revoked" && !ks.Revoke(k.ID) {
			t.Fatal("cannot revoke the revoked test key")
		}
	}
	return ks
}

// newIdentityServer is an MCP server with the gateway's identity installed, as
// wiring_mcp_identity.go installs it with request auth on (no KeylessStatic;
// keylessStaticServer is request auth off), and two tools. Its registered
// servers are m8Org's (m8BindStaticOrg).
func newIdentityServer(t *testing.T, allowAnonymousDev bool) (*Server, *auth.KeyStore) {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
	s.Registry().RegisterServer("gh", []Tool{
		{Name: "t1", InputSchema: json.RawMessage(`{}`)},
		{Name: "t2", InputSchema: json.RawMessage(`{}`)},
	})
	m8BindStaticOrg(s, m8Org)
	ks := newM8KeyStore(t)
	s.SetIdentity(Identity{Keys: ks, AllowAnonymousDev: allowAnonymousDev})
	return s, ks
}

// m8BindStaticOrg makes org the registry's mcp.static.org_id, the org the
// registered (YAML) servers serve: the virtual servers' Registry.SetStaticOrg serves them only
// to that org's principals and to a principal with no org, and these tests'
// keys resolve to m8Org. A direct call, so a
// rename of the virtual servers' method fails to compile instead of turning this off.
func m8BindStaticOrg(s *Server, org string) {
	s.Registry().SetStaticOrg(org)
}

// bearer is the header a client sends its key or token in.
func bearer(token string) map[string]string {
	return map[string]string{"Authorization": "Bearer " + token}
}

// withHeaders returns base plus extra.
func withHeaders(base map[string]string, extra ...string) map[string]string {
	out := make(map[string]string, len(base)+len(extra)/2)
	for k, v := range base {
		out[k] = v
	}
	for i := 0; i+1 < len(extra); i += 2 {
		out[extra[i]] = extra[i+1]
	}
	return out
}

// requireAuthError checks an MCP 401: HTTP 401, the gateway's Bearer
// challenge, a JSON-RPC error -32001 whose data.code is code, and no session.
func requireAuthError(t *testing.T, w *httptest.ResponseRecorder, code, what string) {
	t.Helper()
	requireUnauthorized(t, w, what)
	if got := w.Header().Get("WWW-Authenticate"); !strings.HasPrefix(got, models.BearerChallenge) {
		t.Fatalf("%s: WWW-Authenticate %q, want the gateway's challenge %q", what, got, models.BearerChallenge)
	}
	var msg Message
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
		t.Fatalf("%s: body is not JSON-RPC: %v: %s", what, err, w.Body.String())
	}
	if msg.Error == nil || msg.Error.Code != ErrCodeUnauthorized {
		t.Fatalf("%s: error %+v, want JSON-RPC %d", what, msg.Error, ErrCodeUnauthorized)
	}
	var data struct {
		Code string `json:"code"`
	}
	if err := json.Unmarshal(msg.Error.Data, &data); err != nil || data.Code != code {
		t.Fatalf("%s: error data %s, want code %q", what, msg.Error.Data, code)
	}
	if msg.Result != nil {
		t.Fatalf("%s: a 401 carried a result: %s", what, msg.Result)
	}
}

// mcpRequest sends method on the MCP endpoint straight to its handler.
func mcpRequest(s *Server, method string, headers map[string]string) *httptest.ResponseRecorder {
	var req *http.Request
	if method == http.MethodGet {
		// The stream of an accepted GET lives for the session: bound it.
		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		defer cancel()
		req = httptest.NewRequest(method, "/mcp", nil).WithContext(ctx)
	} else {
		req = httptest.NewRequest(method, "/mcp", nil)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	switch method {
	case http.MethodGet:
		s.HandleSSE(w, req)
	case http.MethodDelete:
		s.HandleDelete(w, req)
	}
	return w
}

// toolsListMsg is a tools/list request.
func toolsListMsg() *Message {
	return &Message{JSONRPC: "2.0", ID: json.RawMessage(`2`), Method: MethodToolsList}
}

func TestAnonymousPostGetDelete401(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	sessionID := initializeSessionWith(t, s, bearer(m8KeyShared))

	initParams, _ := json.Marshal(InitializeParams{ProtocolVersion: ProtocolVersion, ClientInfo: Implementation{Name: "anon", Version: "1"}})
	for _, msg := range []*Message{
		{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: initParams},
		toolsListMsg(),
		{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"gh_t1","arguments":{}}`)},
		{JSONRPC: "2.0", Method: MethodInitialized},
	} {
		requireAuthError(t, postMCP(t, s, msg, map[string]string{"MCP-Session-Id": sessionID}), AuthMissingCredentials, "POST "+msg.Method+" with no credential")
	}
	for _, method := range []string{http.MethodGet, http.MethodDelete} {
		requireAuthError(t, mcpRequest(s, method, map[string]string{"MCP-Session-Id": sessionID}), AuthMissingCredentials, method+" with no credential")
	}
	if n := s.SessionCount(); n != 1 {
		t.Fatalf("anonymous requests changed the session count to %d, want 1", n)
	}

	// The 401 names the gateway's request id, as every MCP answer does.
	w := postMCP(t, s, toolsListMsg(), nil)
	if id := w.Header().Get("x-agentcc-request-id"); !strings.HasPrefix(id, "mcp_") {
		t.Fatalf("a 401 carried request id %q, want mcp_<ULID>", id)
	}

	// The same server serves a valid key.
	w = postMCP(t, s, toolsListMsg(), withHeaders(bearer(m8KeyShared), "MCP-Session-Id", sessionID))
	if resp := decodeResponse(t, w); w.Code != http.StatusOK || resp.Error != nil {
		t.Fatalf("a valid key's tools/list: status %d, error %+v", w.Code, resp.Error)
	}
}

func TestInvalidKey401WithChallenge(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	sessionID := initializeSessionWith(t, s, bearer(m8KeyShared))

	for _, h := range []map[string]string{
		bearer("not-a-key"),
		{"x-api-key": "not-a-key"},
		bearer(m8KeyShared + "x"),
	} {
		h["MCP-Session-Id"] = sessionID
		w := postMCP(t, s, toolsListMsg(), h)
		requireAuthError(t, w, AuthInvalidAPIKey, "an invalid key")
		if got := w.Header().Get("WWW-Authenticate"); got != models.BearerChallenge {
			t.Fatalf("with no resource metadata URL the challenge is %q, want exactly %q", got, models.BearerChallenge)
		}
		for _, method := range []string{http.MethodGet, http.MethodDelete} {
			requireAuthError(t, mcpRequest(s, method, h), AuthInvalidAPIKey, method+" with an invalid key")
		}
	}

	// Once the OAuth step sets the protected-resource metadata URL, the challenge names it.
	const prm = "https://gw.example.test/.well-known/oauth-protected-resource/mcp"
	s.SetDeps(Deps{ResourceMetadataURL: prm})
	w := postMCP(t, s, toolsListMsg(), bearer("not-a-key"))
	requireAuthError(t, w, AuthInvalidAPIKey, "an invalid key with a resource metadata URL")
	if got, want := w.Header().Get("WWW-Authenticate"), models.BearerChallenge+`, resource_metadata="`+prm+`"`; got != want {
		t.Fatalf("challenge %q, want %q", got, want)
	}
	requireAuthError(t, postMCP(t, s, toolsListMsg(), nil), AuthMissingCredentials, "no key with a resource metadata URL")
}

func TestRevokedExpiredIPKeys401(t *testing.T) {
	s, ks := newIdentityServer(t, false)

	cases := []struct {
		name, key, remote, xff, code string
	}{
		{"revoked", m8KeyRevoked, "192.0.2.10:5000", "", AuthInvalidAPIKey},
		{"expired", m8KeyExpired, "192.0.2.10:5000", "", AuthKeyExpired},
		{"outside the key's addresses", m8KeyIP, "192.0.2.10:5000", "", AuthIPNotAllowed},
		// With no trusted proxy the client is
		// the TCP peer, so a caller outside the key's addresses cannot name
		// an allowed one in X-Forwarded-For (or X-Real-IP).
		{"outside, naming an allowed address in X-Forwarded-For", m8KeyIP, "192.0.2.10:5000", "10.0.0.5", AuthIPNotAllowed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for _, method := range []string{http.MethodPost, http.MethodGet, http.MethodDelete} {
				var body *strings.Reader
				if method == http.MethodPost {
					raw, _ := json.Marshal(toolsListMsg())
					body = strings.NewReader(string(raw))
				} else {
					body = strings.NewReader("")
				}
				ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
				req := httptest.NewRequest(method, "/mcp", body).WithContext(ctx)
				req.RemoteAddr = tc.remote
				if method == http.MethodPost {
					// Well-formed but for the key, so only the key is refused.
					req.Header.Set("Content-Type", "application/json")
				}
				req.Header.Set("Authorization", "Bearer "+tc.key)
				if tc.xff != "" {
					req.Header.Set("X-Forwarded-For", tc.xff)
					req.Header.Set("X-Real-IP", tc.xff)
				}
				w := httptest.NewRecorder()
				switch method {
				case http.MethodPost:
					s.HandlePost(w, req)
				case http.MethodGet:
					s.HandleSSE(w, req)
				default:
					s.HandleDelete(w, req)
				}
				cancel()
				requireAuthError(t, w, tc.code, method+" with the "+tc.name+" key")
			}
		})
	}

	t.Run("inside the key's addresses", func(t *testing.T) {
		raw, _ := json.Marshal(InitializeParams{ProtocolVersion: ProtocolVersion, ClientInfo: Implementation{Name: "ip", Version: "1"}})
		body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: raw})
		req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(string(body)))
		req.RemoteAddr = "10.9.8.7:4000"
		// A well-formed POST: the protocol handler answers 415 without the JSON type.
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+m8KeyIP)
		w := httptest.NewRecorder()
		s.HandlePost(w, req)
		if w.Code != http.StatusOK || w.Header().Get("MCP-Session-Id") == "" {
			t.Fatalf("the IP-bound key from inside its range: status %d, body %s", w.Code, w.Body.String())
		}
	})

	// Behind a proxy the gateway trusts (server.trusted_proxies), the client
	// is the X-Forwarded-For entry that proxy wrote: the same rule as the
	// gateway's KeyAuth and ip_acl (middleware.ClientIP).
	t.Run("behind a trusted proxy, by the forwarded address", func(t *testing.T) {
		resolver, err := middleware.NewClientIPResolver(config.ServerConfig{TrustedProxies: []string{"10.1.2.3"}})
		if err != nil {
			t.Fatal(err)
		}
		for _, tc := range []struct {
			name, xff string
			allowed   bool
		}{
			{"a client outside the key's addresses", "192.0.2.10", false},
			{"a client inside the key's addresses", "10.0.0.5", true},
		} {
			raw, _ := json.Marshal(InitializeParams{ProtocolVersion: ProtocolVersion, ClientInfo: Implementation{Name: "ip", Version: "1"}})
			body, _ := json.Marshal(&Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodInitialize, Params: raw})
			req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(string(body)))
			req.RemoteAddr = "10.1.2.3:5000" // the trusted proxy
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+m8KeyIP)
			req.Header.Set("X-Forwarded-For", tc.xff)
			w := httptest.NewRecorder()
			middleware.TrustedClientIP(resolver)(http.HandlerFunc(s.HandlePost)).ServeHTTP(w, req)
			if tc.allowed {
				if w.Code != http.StatusOK || w.Header().Get("MCP-Session-Id") == "" {
					t.Fatalf("%s: status %d, body %s", tc.name, w.Code, w.Body.String())
				}
				continue
			}
			requireAuthError(t, w, AuthIPNotAllowed, tc.name)
		}
	})

	t.Run("a key revoked mid-session is 401 on its next request, never 200", func(t *testing.T) {
		sessionID := initializeSessionWith(t, s, bearer(m8KeyShared))
		h := withHeaders(bearer(m8KeyShared), "MCP-Session-Id", sessionID)
		if w := postMCP(t, s, toolsListMsg(), h); w.Code != http.StatusOK {
			t.Fatalf("before the revocation: status %d", w.Code)
		}
		for _, k := range ks.List() {
			if k.Name == "shared" {
				ks.Revoke(k.ID)
			}
		}
		requireAuthError(t, postMCP(t, s, toolsListMsg(), h), AuthInvalidAPIKey, "tools/list after the key was revoked")
		requireAuthError(t, mcpRequest(s, http.MethodGet, h), AuthInvalidAPIKey, "GET after the key was revoked")
	})
}

// fakeVerifier is a Deps.BearerVerifier: it accepts the tokens in accept,
// refuses those in refuse, and leaves every other token to key auth. It
// records each token it was asked about.
type fakeVerifier struct {
	mu     sync.Mutex
	accept map[string]*Principal
	claims map[string]*models.RuntimeIdentity
	refuse map[string]bool
	asked  []string
}

func (f *fakeVerifier) Verify(ctx context.Context, token, audience string) (*Principal, error) {
	p, _, err := f.VerifyClaims(ctx, token, audience)
	return p, err
}

func (f *fakeVerifier) VerifyClaims(_ context.Context, token, _ string) (*Principal, *models.RuntimeIdentity, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.asked = append(f.asked, token)
	if f.refuse[token] {
		return nil, nil, errors.New("signature does not verify")
	}
	if p, ok := f.accept[token]; ok {
		cp := *p
		return &cp, f.claims[token], nil
	}
	return nil, nil, nil
}

func (f *fakeVerifier) askedAbout() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.asked...)
}

// principalAt runs a tools/list with headers and returns the principal the
// chain resolved, read at step 3 (after hooks 1 and 2).
func principalAt(t *testing.T, s *Server, headers map[string]string) (Principal, *httptest.ResponseRecorder) {
	t.Helper()
	var got Principal
	var mu sync.Mutex
	s.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookResolveTenant {
			mu.Lock()
			got = cc.Principal
			mu.Unlock()
		}
	}
	defer func() { s.hookObserver = nil }()
	w := postMCP(t, s, toolsListMsg(), headers)
	mu.Lock()
	defer mu.Unlock()
	return got, w
}

func TestBearerVerifierConsultedFirst(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	v := &fakeVerifier{
		accept: map[string]*Principal{
			// A token that is also a valid key: the verifier's answer wins.
			// Its scope grants the tools/list below (the OAuth scope check).
			m8KeyShared: {Kind: PrincipalOAuth, OrgID: "org-oauth", UserID: "user-oauth", ClientID: "client-1", Scopes: []string{"mcp:tools"}},
			"rt-token":  {Kind: PrincipalRuntime, OrgID: m8Org, TokenID: "jti-1"},
		},
		refuse: map[string]bool{m8KeyOther: true, "bad-token": true},
	}
	s.SetDeps(Deps{BearerVerifier: v})
	// A session serves only the principal that opened it, so each
	// credential below opens its own, with the verifier already installed.
	oauthSession := initializeSessionWith(t, s, bearer(m8KeyShared))

	p, w := principalAt(t, s, withHeaders(bearer(m8KeyShared), "MCP-Session-Id", oauthSession))
	if w.Code != http.StatusOK || p.Kind != PrincipalOAuth || p.OrgID != "org-oauth" || p.UserID != "user-oauth" || p.KeyID != "" {
		t.Fatalf("a token the verifier accepts: status %d, principal %+v; want the verifier's oauth principal, not the key's", w.Code, p)
	}

	rtSession := initializeSessionWith(t, s, bearer("rt-token"))
	p, w = principalAt(t, s, withHeaders(bearer("rt-token"), "MCP-Session-Id", rtSession))
	if w.Code != http.StatusOK || p.Kind != PrincipalRuntime || p.TokenID != "jti-1" {
		t.Fatalf("a runtime token: status %d, principal %+v", w.Code, p)
	}

	// A token the verifier refuses is 401 invalid_token, even when it is
	// also a valid key: the key store is not a fallback for a bad token. The
	// 401 comes before any session is looked at.
	requireAuthError(t, postMCP(t, s, toolsListMsg(), withHeaders(bearer(m8KeyOther), "MCP-Session-Id", oauthSession)), AuthInvalidToken, "a token the verifier refuses")
	requireAuthError(t, mcpRequest(s, http.MethodGet, bearer("bad-token")), AuthInvalidToken, "GET with a refused token")

	// A token the verifier does not handle is tried as a key.
	personalSession := initializeSessionWith(t, s, bearer(m8KeyPersonal))
	asked := len(v.askedAbout())
	p, w = principalAt(t, s, withHeaders(bearer(m8KeyPersonal), "MCP-Session-Id", personalSession))
	if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.KeyID == "" {
		t.Fatalf("a key the verifier passes on: status %d, principal %+v", w.Code, p)
	}
	if got := v.askedAbout(); len(got) != asked+1 || got[len(got)-1] != m8KeyPersonal {
		t.Fatalf("the verifier was asked %v; it must be asked first, once, about the bearer", got[asked:])
	}

	// x-api-key is a key header, never a bearer: the verifier is not asked.
	keyHeader := map[string]string{"x-api-key": m8KeyPersonal}
	asked = len(v.askedAbout())
	keySession := initializeSessionWith(t, s, keyHeader)
	p, w = principalAt(t, s, withHeaders(keyHeader, "MCP-Session-Id", keySession))
	if w.Code != http.StatusOK || p.Kind != PrincipalKey || len(v.askedAbout()) != asked {
		t.Fatalf("x-api-key: status %d, principal %+v, verifier asked %v", w.Code, p, v.askedAbout()[asked:])
	}

	// The default verifier refuses every JWT until the OAuth step installs an issuer.
	s.SetDeps(Deps{})
	jwt := "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"
	requireAuthError(t, postMCP(t, s, toolsListMsg(), withHeaders(bearer(jwt), "MCP-Session-Id", oauthSession)), AuthInvalidToken, "a JWT with the default verifier")
}

// TestKeyPrincipalResolved pins the person-key contract: a synced key carries
// the reserved, control-plane-owned user_id, never an Owner label or header.
// Config keys and legacy personal markers confer no person identity. Each key
// opens its own session: a session serves only the principal that opened it.
func TestKeyPrincipalResolved(t *testing.T) {
	s, ks := newIdentityServer(t, false)
	ids := map[string]string{}
	for _, k := range ks.List() {
		ids[k.Name] = k.ID
	}
	// resolve opens a session with cred, then sends a tools/list on it with
	// cred and extra headers, and returns the principal the chain resolved.
	resolve := func(cred map[string]string, extra ...string) (Principal, *httptest.ResponseRecorder) {
		t.Helper()
		sessionID := initializeSessionWith(t, s, cred)
		return principalAt(t, s, withHeaders(cred, append([]string{"MCP-Session-Id", sessionID}, extra...)...))
	}

	const syncedRaw = "mcp-person-sync-fixture"
	ks.LoadFromHashes([]auth.SyncedKey{{ID: "person-sync", KeyHash: auth.HashKey(syncedRaw),
		Metadata: map[string]string{"org_id": m8Org, "user_id": mcpPersonU}}})
	p, w := resolve(bearer(syncedRaw), "x-agentcc-user-id", mcpPersonV, "x-agentcc-org-id", "org-evil")
	if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.KeyID != "person-sync" || p.OrgID != m8Org || p.UserID != mcpPersonU {
		t.Fatalf("synced person key: status %d, principal %+v; want its reserved owner identity", w.Code, p)
	}

	p, w = resolve(bearer(m8KeyShared), "x-agentcc-org-id", "org-evil", "x-agentcc-user-id", "user-evil", "x-agentcc-key-id", "key-evil")
	want := Principal{Kind: PrincipalKey, KeyID: ids["shared"], OrgID: m8Org}
	if w.Code != http.StatusOK || p.Kind != want.Kind || p.KeyID != want.KeyID || p.OrgID != want.OrgID || p.UserID != "" {
		t.Fatalf("shared key: status %d, principal %+v, want %+v (a shared org key never carries a user)", w.Code, p, want)
	}

	p, w = resolve(bearer(m8KeyPersonal))
	if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.KeyID != ids["personal"] || p.OrgID != m8Org || p.UserID != "" {
		t.Fatalf("a key marked personal: status %d, principal %+v; want its key and org and no user (its owner %q is a tenant label)", w.Code, p, "user-7")
	}
	// The owner is a label a key update may change; the principal still
	// carries no user, so renaming the owner can never name another user.
	owner := "user-someone-else"
	if _, err := ks.Update(ids["personal"], nil, &owner, nil, nil, nil); err != nil {
		t.Fatalf("change the personal key's owner: %v", err)
	}
	if p, w = resolve(bearer(m8KeyPersonal)); w.Code != http.StatusOK || p.UserID != "" || p.KeyID != ids["personal"] {
		t.Fatalf("after its owner changed, the personal key: status %d, principal %+v; want no user", w.Code, p)
	}

	p, w = resolve(map[string]string{"x-api-key": m8KeyOther})
	if w.Code != http.StatusOK || p.KeyID != ids["other"] || p.OrgID != "org-other" || p.UserID != "" {
		t.Fatalf("another org's key: status %d, principal %+v", w.Code, p)
	}

	// The key's tool lists still filter the listing (the virtual servers' rules read them).
	_, w = resolve(bearer(m8KeyTools))
	var res ListToolsResult
	if err := json.Unmarshal(decodeResponse(t, w).Result, &res); err != nil || len(res.Tools) != 1 || res.Tools[0].Name != "gh_t1" {
		t.Fatalf("the key allows only gh_t1; listed %+v (%v)", res.Tools, err)
	}
	_, w = resolve(bearer(m8KeyShared))
	if err := json.Unmarshal(decodeResponse(t, w).Result, &res); err != nil || len(res.Tools) != 2 {
		t.Fatalf("the shared key has no tool list; listed %+v (%v)", res.Tools, err)
	}
}

// TestChainCallWithoutAPrincipalIsRefused: hook 1 refuses a chained call on a
// server with an identity whose request never passed authenticateHTTP, so a
// handler that skips the chokepoint cannot reach a tool.
func TestChainCallWithoutAPrincipalIsRefused(t *testing.T) {
	s, _ := newIdentityServer(t, false)
	cc := s.newCallContext(httptest.NewRequest(http.MethodPost, "/mcp", nil), &Message{Method: MethodToolsCall, ID: json.RawMessage(`1`)}, nil)
	s.runChain(cc, toolsCallChain)
	d := cc.Decision
	if d == nil || d.Code != AuthMissingCredentials || d.HTTPStatus != http.StatusUnauthorized || d.RPCCode != ErrCodeUnauthorized {
		t.Fatalf("decision %+v, want 401 missing_credentials", d)
	}
	if got := d.Header.Get("WWW-Authenticate"); !strings.HasPrefix(got, models.BearerChallenge) {
		t.Fatalf("challenge %q", got)
	}
	if cc.Executed || cc.Dispatched {
		t.Fatal("a call with no principal ran")
	}

	// With no identity installed (a bare server, as unit tests build) the
	// chain runs as before.
	bare := newTestServer(t)
	defer bare.Close()
	cc = bare.newCallContext(httptest.NewRequest(http.MethodPost, "/mcp", nil), &Message{Method: MethodToolsList, ID: json.RawMessage(`1`)}, nil)
	bare.runChain(cc, toolsListChain)
	if cc.Decision != nil {
		t.Fatalf("a bare server refused %+v", cc.Decision)
	}
}
