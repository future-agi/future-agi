package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
)

// The resource server tests' deployment. Every value is a placeholder.
const (
	oauthTestPublicURL = "https://gw.example.test"
	oauthTestIssuer    = "https://app.example.test/agentcc/mcp-oauth"
	oauthTestInit      = `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}`
)

// newOAuthTestServer is a gateway with MCP on and mcp.oauth as given.
func newOAuthTestServer(t *testing.T, oauthOn bool, mutate func(*config.Config)) *Server {
	t.Helper()
	return newMCPTestServer(t, true, func(c *config.Config) {
		c.MCP.PublicURL = oauthTestPublicURL
		c.MCP.ClientOAuthEnabled = oauthOn
		c.MCP.OAuth.Enabled = oauthOn
		c.MCP.OAuth.Issuer = oauthTestIssuer
		if mutate != nil {
			mutate(c)
		}
	})
}

// installResourceServer installs what wiring_mcp_oauth.go installs: the
// gateway's identity, the verifier and the endpoint's metadata URL.
func installResourceServer(t *testing.T, srv *Server) {
	t.Helper()
	res, err := oauthrs.NewResource(oauthrs.ConfigFrom(srv.cfg.MCP))
	if err != nil {
		t.Fatal(err)
	}
	m := srv.MCP()
	m.SetIdentity(mcp.Identity{Keys: auth.NewKeyStore(config.AuthConfig{})})
	m.SetDeps(mcp.Deps{
		BearerVerifier:      oauthrs.NewVerifier(res, oauthrs.NewKeySet(res.Issuer(), nil, nil), nil),
		ResourceMetadataURL: res.MetadataURL(""),
	})
}

func serveRequest(srv *Server, req *http.Request) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func pushKeys(t *testing.T, srv *Server, auth string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("PUT", "/-/mcp/oauth/keys", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	return serveRequest(srv, req)
}

func getMetadata(t *testing.T, srv *Server, path string, host string) (*httptest.ResponseRecorder, oauthrs.Metadata) {
	t.Helper()
	req := httptest.NewRequest("GET", path, nil)
	if host != "" {
		req.Host = host
		req.Header.Set("X-Forwarded-Host", host)
	}
	w := serveRequest(srv, req)
	var doc oauthrs.Metadata
	if w.Code == http.StatusOK {
		if err := json.Unmarshal(w.Body.Bytes(), &doc); err != nil {
			t.Fatalf("GET %s: body is not metadata: %v: %s", path, err, w.Body.String())
		}
	}
	return w, doc
}

// The MCP endpoint and every virtual server publish their protected-resource
// metadata, without a credential, cacheable, naming the configured
// authorization server and their own address from the configured public
// base, whatever host the request names.
func TestPRMRoutesServeMetadata(t *testing.T) {
	srv := newOAuthTestServer(t, true, nil)
	for path, resource := range map[string]string{
		"/.well-known/oauth-protected-resource/mcp":                                 "https://gw.example.test/mcp",
		"/.well-known/oauth-protected-resource/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X": "https://gw.example.test/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X",
		"/.well-known/oauth-protected-resource/mcp/v/vs_never_created":              "https://gw.example.test/mcp/v/vs_never_created",
	} {
		w, doc := getMetadata(t, srv, path, "evil.example.test")
		if w.Code != http.StatusOK {
			t.Fatalf("GET %s = %d %s, want 200", path, w.Code, w.Body.String())
		}
		if doc.Resource != resource {
			t.Fatalf("GET %s: resource %q, want %q", path, doc.Resource, resource)
		}
		if len(doc.AuthorizationServers) != 1 || doc.AuthorizationServers[0] != oauthTestIssuer {
			t.Fatalf("GET %s: authorization_servers %v", path, doc.AuthorizationServers)
		}
		if cc := w.Header().Get("Cache-Control"); !strings.HasPrefix(cc, "public, max-age=") {
			t.Fatalf("GET %s: Cache-Control %q, want public and cacheable", path, cc)
		}
		if ct := w.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
			t.Fatalf("GET %s: Content-Type %q", path, ct)
		}
	}

	custom := newOAuthTestServer(t, true, func(c *config.Config) { c.MCP.Endpoint = "/tools/mcp" })
	w, doc := getMetadata(t, custom, "/.well-known/oauth-protected-resource/tools/mcp/v/vs_1", "")
	if w.Code != http.StatusOK || doc.Resource != "https://gw.example.test/tools/mcp/v/vs_1" {
		t.Fatalf("custom endpoint metadata = %d %+v", w.Code, doc)
	}
}

// With mcp.oauth off (the release default) there is no metadata and no key
// push route: the gateway advertises no authorization server.
func TestPRMRoutesAbsentWhenOAuthOff(t *testing.T) {
	srv := newOAuthTestServer(t, false, nil)
	for _, path := range []string{"/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource/mcp/v/vs_A"} {
		if w, _ := getMetadata(t, srv, path, ""); w.Code != http.StatusNotFound {
			t.Fatalf("GET %s with oauth off = %d, want 404", path, w.Code)
		}
	}
	if w := pushKeys(t, srv, "Bearer mcp-admin", []byte(`{}`)); w.Code != http.StatusNotFound {
		t.Fatalf("PUT /-/mcp/oauth/keys with oauth off = %d, want 404", w.Code)
	}
}

// Only the admin token pushes verification keys; a push is applied when its
// version is newer and refused whole when it is not a valid public key set
// for the configured issuer.
func TestKeysPushNeedsAdminToken(t *testing.T) {
	srv := newOAuthTestServer(t, true, nil)
	signer := oauthrstest.NewSigner(t, "as-route")
	body := oauthrstest.PushBody(t, oauthTestIssuer, 1, signer)

	// Before the wiring installs the resource server, nothing is accepted.
	if w := pushKeys(t, srv, "Bearer mcp-admin", body); w.Code != http.StatusServiceUnavailable {
		t.Fatalf("push with no resource server = %d %s, want 503", w.Code, w.Body.String())
	}
	installResourceServer(t, srv)
	for label, token := range map[string]string{"no token": "", "wrong token": "Bearer not-the-admin-token", "a key": "Bearer sk-agentcc-test"} {
		if w := pushKeys(t, srv, token, body); w.Code != http.StatusUnauthorized {
			t.Fatalf("push with %s = %d %s, want 401", label, w.Code, w.Body.String())
		}
	}
	w := pushKeys(t, srv, "Bearer mcp-admin", body)
	var res oauthrs.PushResult
	if w.Code != http.StatusOK || json.Unmarshal(w.Body.Bytes(), &res) != nil || !res.Applied || res.Version != 1 {
		t.Fatalf("admin push = %d %s, want 200 applied at version 1", w.Code, w.Body.String())
	}
	if w = pushKeys(t, srv, "Bearer mcp-admin", body); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"applied":false`) {
		t.Fatalf("repeated push = %d %s, want 200 not applied", w.Code, w.Body.String())
	}
	k := signer.JWK()
	k["d"] = k["x"]
	bad, _ := json.Marshal(map[string]any{"issuer": oauthTestIssuer, "version": 2, "keys": []any{k}})
	if w = pushKeys(t, srv, "Bearer mcp-admin", bad); w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "invalid_keys_push") {
		t.Fatalf("push with a private member = %d %s, want 400 invalid_keys_push", w.Code, w.Body.String())
	}
}

// postMCPPath posts body to path with the given bearer token and session.
func postMCPPath(srv *Server, path, body, token, session string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if session != "" {
		req.Header.Set("MCP-Session-Id", session)
	}
	return serveRequest(srv, req)
}

// A token issued for one virtual server opens that server and no other: on
// another virtual server, or on the endpoint itself, it is a 401 whose
// challenge sends the client to the metadata of the address it called, and
// that metadata names that address.
func TestTokenForOneVirtualServerOpensOnlyThatServer(t *testing.T) {
	srv := newOAuthTestServer(t, true, nil)
	installResourceServer(t, srv)
	signer := oauthrstest.NewSigner(t, "as-e2e")
	if w := pushKeys(t, srv, "Bearer mcp-admin", oauthrstest.PushBody(t, oauthTestIssuer, 1, signer)); w.Code != http.StatusOK {
		t.Fatalf("key push = %d %s", w.Code, w.Body.String())
	}
	token := signer.Sign(t, oauthrstest.AccessClaims(oauthTestIssuer, "https://gw.example.test/mcp/v/vs_A", time.Now(), 5*time.Minute))

	w := postMCPPath(srv, "/mcp/v/vs_A", oauthTestInit, token, "")
	session := w.Header().Get("MCP-Session-Id")
	if w.Code != http.StatusOK || session == "" {
		t.Fatalf("initialize on vs_A with its token = %d %s, want 200 with a session", w.Code, w.Body.String())
	}
	w = postMCPPath(srv, "/mcp/v/vs_A", `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`, token, session)
	if w.Code == http.StatusUnauthorized || w.Code == http.StatusForbidden {
		t.Fatalf("tools/list on vs_A with its token = %d %s, want past authentication", w.Code, w.Body.String())
	}

	for path, metadata := range map[string]string{
		"/mcp/v/vs_B": "https://gw.example.test/.well-known/oauth-protected-resource/mcp/v/vs_B",
		"/mcp":        "https://gw.example.test/.well-known/oauth-protected-resource/mcp",
	} {
		for label, tok := range map[string]string{"vs_A's token": token, "no credential": ""} {
			w := postMCPPath(srv, path, oauthTestInit, tok, "")
			if w.Code != http.StatusUnauthorized || w.Header().Get("MCP-Session-Id") != "" {
				t.Fatalf("initialize on %s with %s = %d %s, want 401 and no session", path, label, w.Code, w.Body.String())
			}
			challenge := w.Header().Get("WWW-Authenticate")
			if !strings.Contains(challenge, `resource_metadata="`+metadata+`"`) {
				t.Fatalf("401 on %s with %s: challenge %q, want resource_metadata %q", path, label, challenge, metadata)
			}
			mw, doc := getMetadata(t, srv, strings.TrimPrefix(metadata, oauthTestPublicURL), "")
			if mw.Code != http.StatusOK || doc.Resource != oauthTestPublicURL+path {
				t.Fatalf("the metadata the 401 on %s names = %d %+v, want resource %q", path, mw.Code, doc, oauthTestPublicURL+path)
			}
		}
	}
}

func TestDeploymentClientOAuthGateHidesMetadataWithLegacyOAuthOn(t *testing.T) {
	srv := newOAuthTestServer(t, true, func(c *config.Config) { c.MCP.ClientOAuthEnabled = false })
	for _, path := range []string{"/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource/mcp/v/vs_A"} {
		if w, _ := getMetadata(t, srv, path, ""); w.Code != http.StatusNotFound {
			t.Fatalf("GET %s = %d", path, w.Code)
		}
	}
	if w := pushKeys(t, srv, "Bearer mcp-admin", []byte(`{}`)); w.Code != http.StatusNotFound {
		t.Fatalf("keys push = %d", w.Code)
	}
	if srv.keyScopes.mcpSignIn {
		t.Fatal("OFF deployment bypasses the API-key gate for OAuth")
	}
}
