package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// With request auth on (every deployment that sets a gateway key) and MCP
// sign-in on, an MCP client that holds only the access token its person
// signed in for still reaches the MCP endpoint, whose own sign-in check
// decides: it serves the token's server, and it answers a request with no
// usable credential with the challenge that names where to sign in. A
// request with a gateway key is checked by the key gate exactly as before,
// and without MCP sign-in nothing changes.

const (
	signInGateKey       = "sk-agentcc-sign-in-gate-console-key-0000001"
	signInGateScopedKey = "sk-agentcc-sign-in-gate-models-only-key-0002"
	signInGateOrg       = "org-sign-in-gate"
)

// newSignInGateServer is a gateway with request auth on (two keys of one
// org, the second scoped to the model routes only), MCP on, and MCP
// sign-in as given, with the resource server installed when it is on.
func newSignInGateServer(t *testing.T, signIn bool) *Server {
	t.Helper()
	srv := newOAuthTestServer(t, signIn, func(c *config.Config) {
		c.Auth.Enabled = true
		c.Auth.Keys = []config.AuthKeyConfig{
			{Name: "console", Key: signInGateKey, Owner: "person", Metadata: map[string]string{"org_id": signInGateOrg}},
			{Name: "models-only", Key: signInGateScopedKey, Owner: "agent",
				Metadata: map[string]string{"org_id": signInGateOrg, "scopes": "models"}},
		}
	})
	if signIn {
		installResourceServer(t, srv)
	}
	// The MCP identity holds the gateway's own keys, as wiring_mcp_identity.go
	// installs it.
	srv.MCP().SetIdentity(mcp.Identity{Keys: srv.keyStore})
	return srv
}

func TestSignedInClientReachesTheMCPEndpointWithRequestAuthOn(t *testing.T) {
	srv := newSignInGateServer(t, true)
	signer := oauthrstest.NewSigner(t, "as-key-gate")
	if w := pushKeys(t, srv, "Bearer mcp-admin", oauthrstest.PushBody(t, oauthTestIssuer, 1, signer)); w.Code != http.StatusOK {
		t.Fatalf("key push = %d %s", w.Code, w.Body.String())
	}
	token := signer.Sign(t, oauthrstest.AccessClaims(oauthTestIssuer, "https://gw.example.test/mcp/v/vs_A", time.Now(), 5*time.Minute))

	w := postMCPPath(srv, "/mcp/v/vs_A", oauthTestInit, token, "")
	session := w.Header().Get("MCP-Session-Id")
	if w.Code != http.StatusOK || session == "" {
		t.Fatalf("initialize on vs_A with its access token and request auth on = %d %s, want 200 with a session", w.Code, w.Body.String())
	}
	w = postMCPPath(srv, "/mcp/v/vs_A", `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`, token, session)
	if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
		t.Fatalf("tools/list on vs_A with its access token = %d %s, want past authentication", w.Code, w.Body.String())
	}

	// No usable credential: the endpoint's challenge, which sends the client
	// to sign in for the address it called.
	metadata := "https://gw.example.test/.well-known/oauth-protected-resource/mcp/v/vs_A"
	for label, tok := range map[string]string{
		"no credential":        "",
		"a broken token":       token[:len(token)-4] + "AAAA",
		"an unknown key":       "sk-agentcc-not-a-key-of-this-gateway-000000",
		"another server's one": signer.Sign(t, oauthrstest.AccessClaims(oauthTestIssuer, "https://gw.example.test/mcp/v/vs_B", time.Now(), 5*time.Minute)),
	} {
		w := postMCPPath(srv, "/mcp/v/vs_A", oauthTestInit, tok, "")
		if w.Code != http.StatusUnauthorized || w.Header().Get("MCP-Session-Id") != "" {
			t.Fatalf("initialize on vs_A with %s = %d %s, want 401 and no session", label, w.Code, w.Body.String())
		}
		if challenge := w.Header().Get("WWW-Authenticate"); !strings.Contains(challenge, `resource_metadata="`+metadata+`"`) {
			t.Fatalf("401 on vs_A with %s: challenge %q, want resource_metadata %q (body %s)", label, challenge, metadata, w.Body.String())
		}
	}

	// A gateway key still opens the endpoint as before.
	if w := postMCPPath(srv, "/mcp", oauthTestInit, signInGateKey, ""); w.Code != http.StatusOK {
		t.Fatalf("initialize on /mcp with a gateway key = %d %s, want 200", w.Code, w.Body.String())
	}
}

// A gateway key meets the key gate with or without MCP sign-in: a key whose
// scope leaves out MCP is refused with the scope's refusal.
func TestScopedKeyStillMeetsTheKeyGateWithSignInOn(t *testing.T) {
	for _, signIn := range []bool{false, true} {
		srv := newSignInGateServer(t, signIn)
		w := postMCPPath(srv, "/mcp", oauthTestInit, signInGateScopedKey, "")
		if !scopeDenied(t, w.Code, w.Body.String()) {
			t.Fatalf("sign-in %v: POST /mcp with a models-only key = %d %s, want 403 %s", signIn, w.Code, w.Body.String(), codeScopeDeny)
		}
	}
}

// Without MCP sign-in the endpoint stays behind the key gate: no key, no
// entry, and no sign-in challenge is offered.
func TestWithoutSignInTheMCPEndpointStillNeedsAKey(t *testing.T) {
	srv := newSignInGateServer(t, false)
	w := postMCPPath(srv, "/mcp", oauthTestInit, "", "")
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("POST /mcp with no key and sign-in off = %d %s, want 401", w.Code, w.Body.String())
	}
	if challenge := w.Header().Get("WWW-Authenticate"); strings.Contains(challenge, "resource_metadata") {
		t.Fatalf("POST /mcp with sign-in off offered a sign-in challenge %q", challenge)
	}
}

// An access token opens the MCP endpoint and nothing else: on every other
// gated route it is no key, so the key gate refuses it as before, with or
// without MCP sign-in.
func TestAccessTokenOpensNoRouteOutsideTheMCPEndpoint(t *testing.T) {
	srv := newSignInGateServer(t, true)
	signer := oauthrstest.NewSigner(t, "as-key-gate-v1")
	if w := pushKeys(t, srv, "Bearer mcp-admin", oauthrstest.PushBody(t, oauthTestIssuer, 1, signer)); w.Code != http.StatusOK {
		t.Fatalf("key push = %d %s", w.Code, w.Body.String())
	}
	for _, aud := range []string{"https://gw.example.test/mcp/v/vs_A", "https://gw.example.test/mcp"} {
		token := signer.Sign(t, oauthrstest.AccessClaims(oauthTestIssuer, aud, time.Now(), 5*time.Minute))
		for _, route := range []struct{ method, path, body string }{
			{"POST", "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`},
			{"GET", "/v1/models", ""},
		} {
			req := httptest.NewRequest(route.method, route.path, strings.NewReader(route.body))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+token)
			if w := serveRequest(srv, req); w.Code != http.StatusUnauthorized || !strings.Contains(w.Body.String(), "invalid_api_key") {
				t.Fatalf("%s %s with an access token for %s = %d %s, want 401 invalid_api_key", route.method, route.path, aud, w.Code, w.Body.String())
			}
		}
	}
}

// signInAddressOrg is the organization oauthrstest.AccessClaims signs for.
const signInAddressOrg = "org-oauth-test"

// A signed-in client meets its organization's address rules exactly as that
// organization's keys do: from an address the organization's ip_acl allows
// it is served, from any other it is refused and no session is opened.
func TestSignedInClientMeetsItsOrganizationsAddressRules(t *testing.T) {
	t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "mcp-admin"
	cfg.MCP.Enabled = true
	cfg.MCP.PublicURL = oauthTestPublicURL
	cfg.MCP.ClientOAuthEnabled = true
	cfg.MCP.OAuth.Enabled = true
	cfg.MCP.OAuth.Issuer = oauthTestIssuer
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "console", Key: signInGateKey, Owner: "person", Metadata: map[string]string{"org_id": signInAddressOrg}},
	}
	cfg.IPACL.Enabled = true
	tenants := tenant.NewStore()
	tenants.Set(signInAddressOrg, &tenant.OrgConfig{IPACL: &tenant.IPACLConfig{Enabled: true, Allow: []string{"198.51.100.0/24"}}})
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.MCP().Close() })
	installResourceServer(t, srv)
	srv.MCP().SetIdentity(mcp.Identity{Keys: srv.keyStore})

	signer := oauthrstest.NewSigner(t, "as-key-gate-ip")
	if w := pushKeys(t, srv, "Bearer mcp-admin", oauthrstest.PushBody(t, oauthTestIssuer, 1, signer)); w.Code != http.StatusOK {
		t.Fatalf("key push = %d %s", w.Code, w.Body.String())
	}
	token := signer.Sign(t, oauthrstest.AccessClaims(oauthTestIssuer, "https://gw.example.test/mcp/v/vs_A", time.Now(), 5*time.Minute))
	from := func(ip, credential string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/mcp/v/vs_A", strings.NewReader(oauthTestInit))
		req.RemoteAddr = ip + ":41234"
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+credential)
		return serveRequest(srv, req)
	}

	if w := from("198.51.100.7", token); w.Code != http.StatusOK || w.Header().Get("MCP-Session-Id") == "" {
		t.Fatalf("signed-in initialize from an allowed address = %d %s, want 200 with a session", w.Code, w.Body.String())
	}
	for label, credential := range map[string]string{"its access token": token, "the organization's key": signInGateKey} {
		w := from("203.0.113.9", credential)
		if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "Access denied: IP address is not allowed") {
			t.Fatalf("initialize with %s from an address the organization does not allow = %d %s, want 403", label, w.Code, w.Body.String())
		}
		if w.Header().Get("MCP-Session-Id") != "" {
			t.Fatalf("initialize with %s from a refused address opened a session", label)
		}
	}
}
