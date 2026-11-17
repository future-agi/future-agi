package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// CN22c: an Omega EE agent key carries scopes "mcp,models" (reserved key
// metadata only the control plane sets). The key gate admits it on the MCP
// endpoint and on POST /v1/chat/completions, /v1/embeddings and /v1/responses,
// and answers every other route, key-gated or not, 403 key_scope_denied
// before any handler runs. These tests drive every registered route through
// the full middleware chain, so a route added later is covered too.

const (
	scopedKey      = "sk-agentcc-scoped-ee-agent-key-000000000001"
	scopedKeyID    = "key_1"
	unscopedKey    = "sk-agentcc-unscoped-console-key-00000000002"
	scopeAdminTok  = "key-scope-admin-token-0123456789abcdef"
	scopeMCPPath   = "/mcp"
	codeScopeDeny  = "key_scope_denied"
	scopeTestOrgID = "org-scope"
)

func newKeyScopeTestServer(t *testing.T, scopes string) *Server {
	t.Helper()
	mock := startMockOpenAI(t)
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = scopeAdminTok
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "ee-agent", Key: scopedKey, Owner: "omega-ee",
			Metadata: map[string]string{"org_id": scopeTestOrgID, "scopes": scopes}},
		{Name: "console", Key: unscopedKey, Owner: "person",
			Metadata: map[string]string{"org_id": scopeTestOrgID}},
	}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "scope", Description: "scope", Version: "1.0.0"}
	cfg.MCP.Enabled = true
	cfg.Cluster.Enabled = true
	cfg.Edge.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: mock.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, metrics.NewRegistry(), testModelDBPtr(), nil, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

// modelScopeRoutes are the routes the models scope admits.
var modelScopeRoutes = map[string]bool{
	"POST /v1/chat/completions": true,
	"POST /v1/embeddings":       true,
	"POST /v1/responses":        true,
}

func scopeDenied(t *testing.T, code int, body string) bool {
	t.Helper()
	if code != http.StatusForbidden {
		return false
	}
	var parsed struct {
		Error struct {
			Type string `json:"type"`
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal([]byte(body), &parsed); err != nil {
		t.Fatalf("403 body is not the model path's error object: %v: %s", err, body)
	}
	if parsed.Error.Code == codeScopeDeny && parsed.Error.Type != "permission_error" {
		t.Fatalf("key_scope_denied with type %q, want permission_error", parsed.Error.Type)
	}
	return parsed.Error.Code == codeScopeDeny
}

func TestScopedKeyIsRefusedOnEveryRouteOutsideItsScope(t *testing.T) {
	srv := newKeyScopeTestServer(t, "mcp,models")
	routes := registeredRoutes(t, srv, "/")
	requireExemplars(t, routes,
		"GET /v1/models", "POST /v1/chat/completions", "POST /v1/completions",
		"POST /v1/images/generations", "GET /v1/responses/{id}", "POST /v1/messages",
		"POST /v1/a2a", "GET /-/keys", "POST /-/keys", "GET /-/orgs/{org_id}/config",
		"POST /v1beta/models/{model_action}", "GET /healthz",
	)

	for _, rt := range routes {
		target := concretePath(rt.Pattern)
		name := rt.Method + " " + rt.Pattern
		t.Run(name, func(t *testing.T) {
			w := serveCanonical(t, srv, rt.Method, target, "Bearer "+scopedKey)
			denied := scopeDenied(t, w.Code, w.Body.String())
			switch {
			case modelScopeRoutes[name] || strings.HasPrefix(rt.Pattern, scopeMCPPath):
				if denied {
					t.Fatalf("%s is in the key's scope but was refused: %s", name, w.Body.String())
				}
			case !denied:
				t.Fatalf("%s with the scoped key: status %d, want 403 %s. Body: %s",
					name, w.Code, codeScopeDeny, w.Body.String())
			}
		})
	}
}

func TestScopedKeyIsRefusedOnEverySpellingOfAnOutOfScopePath(t *testing.T) {
	srv := newKeyScopeTestServer(t, "mcp,models")
	for _, target := range []string{"/v1/models", "//v1/models", "/v1/./models", "/v1/chat/../models", "/x/../v1/models"} {
		w := serveCanonical(t, srv, http.MethodGet, target, "Bearer "+scopedKey)
		if !scopeDenied(t, w.Code, w.Body.String()) {
			t.Fatalf("GET %s: status %d, want 403 %s. Body: %s", target, w.Code, codeScopeDeny, w.Body.String())
		}
	}
	// The x-api-key header is the same key.
	req := serveScopeHeader(t, srv, http.MethodGet, "/-/keys", "x-api-key", scopedKey)
	if !scopeDenied(t, req.Code, req.Body.String()) {
		t.Fatalf("GET /-/keys with the scoped key in x-api-key: status %d, want 403 %s", req.Code, codeScopeDeny)
	}
	// The Gemini routes take a key from x-goog-api-key or ?key=, outside the
	// key gate: the scope still answers before the handler resolves anything.
	gemini := "/v1beta/models/gpt-4o:generateContent"
	req = serveScopeHeader(t, srv, http.MethodPost, gemini, "x-goog-api-key", scopedKey)
	if !scopeDenied(t, req.Code, req.Body.String()) {
		t.Fatalf("POST %s with x-goog-api-key: status %d, want 403 %s. Body: %s", gemini, req.Code, codeScopeDeny, req.Body.String())
	}
	req = serveScopeHeader(t, srv, http.MethodPost, gemini+"?key="+scopedKey, "x-unrelated", "1")
	if !scopeDenied(t, req.Code, req.Body.String()) {
		t.Fatalf("POST %s?key=: status %d, want 403 %s. Body: %s", gemini, req.Code, codeScopeDeny, req.Body.String())
	}
}

func TestScopedKeyMethodMustMatchTheModelRoute(t *testing.T) {
	srv := newKeyScopeTestServer(t, "mcp,models")
	// GET on a model path is not a model call; the router would answer 405,
	// but the scope answers first.
	w := serveCanonical(t, srv, http.MethodGet, "/v1/chat/completions", "Bearer "+scopedKey)
	if !scopeDenied(t, w.Code, w.Body.String()) {
		t.Fatalf("GET /v1/chat/completions: status %d, want 403 %s", w.Code, codeScopeDeny)
	}
}

func TestMCPOnlyScopeRefusesTheModelRoutes(t *testing.T) {
	srv := newKeyScopeTestServer(t, "mcp")
	for route := range modelScopeRoutes {
		method, path, _ := strings.Cut(route, " ")
		w := serveCanonical(t, srv, method, path, "Bearer "+scopedKey)
		if !scopeDenied(t, w.Code, w.Body.String()) {
			t.Fatalf("%s with an mcp-only key: status %d, want 403 %s", route, w.Code, codeScopeDeny)
		}
	}
	w := serveCanonical(t, srv, http.MethodPost, scopeMCPPath, "Bearer "+scopedKey)
	if scopeDenied(t, w.Code, w.Body.String()) {
		t.Fatalf("POST /mcp with an mcp-only key was refused: %s", w.Body.String())
	}
}

func TestUnreadableScopeAllowsNothing(t *testing.T) {
	for _, scopes := range []string{"", " ", "mcp,admin", "models,models", "MCP"} {
		srv := newKeyScopeTestServer(t, scopes)
		for _, c := range []struct{ method, path string }{
			{http.MethodPost, "/v1/chat/completions"}, {http.MethodPost, scopeMCPPath}, {http.MethodGet, "/v1/models"},
		} {
			w := serveCanonical(t, srv, c.method, c.path, "Bearer "+scopedKey)
			if !scopeDenied(t, w.Code, w.Body.String()) {
				t.Fatalf("scopes %q, %s %s: status %d, want 403 %s (fail closed)", scopes, c.method, c.path, w.Code, codeScopeDeny)
			}
		}
	}
}

func TestUnscopedKeysAreUntouched(t *testing.T) {
	srv := newKeyScopeTestServer(t, "mcp,models")
	routes := registeredRoutes(t, srv, "/")
	for _, rt := range routes {
		w := serveCanonical(t, srv, rt.Method, concretePath(rt.Pattern), "Bearer "+unscopedKey)
		if scopeDenied(t, w.Code, w.Body.String()) {
			t.Fatalf("%s %s with an unscoped key answered %s", rt.Method, rt.Pattern, codeScopeDeny)
		}
	}
	// The admin token on an admin route is not a key at all.
	w := serveCanonical(t, srv, http.MethodGet, "/-/keys", "Bearer "+scopeAdminTok)
	if w.Code != http.StatusOK {
		t.Fatalf("GET /-/keys with the admin token: %d, want 200: %s", w.Code, w.Body.String())
	}
}

func serveScopeHeader(t *testing.T, srv *Server, method, target, header, value string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, target, nil)
	req.Header.Set(header, value)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}
