package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// modelRecorder is an OpenAI-shaped upstream that records the model of each
// chat request it serves.
type modelRecorder struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string
}

func startModelRecorder(t *testing.T) *modelRecorder {
	t.Helper()
	m := &modelRecorder{}
	m.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		var req struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		m.mu.Lock()
		m.seen = append(m.seen, req.Model)
		m.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "chatcmpl-seams", "object": "chat.completion", "created": 1700000000, "model": req.Model,
			"choices": []any{map[string]any{"index": 0, "message": map[string]string{"role": "assistant", "content": "ok"}, "finish_reason": "stop"}},
			"usage":   map[string]int{"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
		})
	}))
	t.Cleanup(m.Close)
	return m
}

func (m *modelRecorder) models() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.seen...)
}

// newRouteTestServer serves gpt-4o and gpt-4o-mini through "openai", with an
// access group "basic" (gpt-4o-mini only, alias "fast"), a provider lock that
// refuses "openai-denied", and three keys: an internal key in "basic", a plain
// internal key, and a BYOK key, which may not use the global providers.
func newRouteTestServer(t *testing.T, upstreamURL string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "basic", Key: "sk-route-basic", Owner: "t", KeyType: "internal", Metadata: map[string]string{"access_groups": "basic"}},
		{Name: "internal", Key: "sk-route-internal", Owner: "t", KeyType: "internal"},
		{Name: "byok", Key: "sk-route-byok", Owner: "t"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai",
		Models: []string{"gpt-4o", "gpt-4o-mini"},
	}
	cfg.Providers["openai-denied"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai",
		Models: []string{"denied-model"},
	}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{
		"basic": {Models: []string{"gpt-4o-mini"}, Aliases: map[string]string{"fast": "gpt-4o-mini"}},
	}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openai-denied"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

// TestResolveRouteExitPaths pins every way ChatCompletion's route resolution
// can end, through the real handler: each refusal's exact status and body, and
// the model and provider a served request reaches.
func TestResolveRouteExitPaths(t *testing.T) {
	cases := []struct {
		name       string
		key        string
		model      string
		lock       string
		wantStatus int
		wantBody   string // exact error body; empty for a served request
		wantModel  string // model the upstream received; empty when refused
	}{
		{
			name: "access group alias rewrites the model", key: "sk-route-basic", model: "fast",
			wantStatus: 200, wantModel: "gpt-4o-mini",
		},
		{
			name: "access group refuses a model outside the group", key: "sk-route-basic", model: "gpt-4o",
			wantStatus: 403,
			wantBody:   `{"error":{"message":"Model \"gpt-4o\" is not available for this API key. Available groups: basic: gpt-4o-mini","type":"permission_error","param":null,"code":"permission_denied"}}`,
		},
		{
			name: "provider lock to a denied provider", key: "sk-route-internal", model: "gpt-4o", lock: "openai-denied",
			wantStatus: 403,
			wantBody:   `{"error":{"message":"provider \"openai-denied\" is in the deny list and cannot be locked to","type":"permission_error","param":null,"code":"permission_denied"}}`,
		},
		{
			name: "provider lock to an unknown provider", key: "sk-route-internal", model: "gpt-4o", lock: "nope",
			wantStatus: 404,
			wantBody:   `{"error":{"message":"Locked provider \"nope\" not found","type":"not_found","param":null,"code":"provider_not_found"}}`,
		},
		{
			name: "provider lock serves through the locked provider", key: "sk-route-internal", model: "gpt-4o", lock: "openai",
			wantStatus: 200, wantModel: "gpt-4o",
		},
		{
			name: "auto picks a configured model", key: "sk-route-internal", model: "auto",
			wantStatus: 200, wantModel: "gpt-4o-mini",
		},
		{
			name: "unknown model", key: "sk-route-internal", model: "no-such-model",
			wantStatus: 404,
			wantBody:   `{"error":{"message":"model \"no-such-model\" not found in any configured provider. Configure model_map or use 'provider/model' format.","type":"not_found","param":null,"code":"model_not_found"}}`,
		},
		{
			name: "a BYOK key may not use a global provider", key: "sk-route-byok", model: "gpt-4o",
			wantStatus: 403,
			wantBody:   `{"error":{"message":"model \"gpt-4o\" is not available for this API key","type":"permission_error","param":null,"code":"permission_denied"}}`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			up := startModelRecorder(t)
			srv := newRouteTestServer(t, up.URL)
			body := `{"model":"` + tc.model + `","messages":[{"role":"user","content":"hi"}]}`
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+tc.key)
			if tc.lock != "" {
				req.Header.Set("x-agentcc-provider-lock", tc.lock)
			}
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			if w.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d; body %s", w.Code, tc.wantStatus, w.Body.String())
			}
			if tc.wantBody != "" {
				if got := strings.TrimSpace(w.Body.String()); got != tc.wantBody {
					t.Fatalf("body = %s\nwant   %s", got, tc.wantBody)
				}
			}
			var wantSeen []string
			if tc.wantModel != "" {
				wantSeen = []string{tc.wantModel}
				if got := w.Header().Get("x-agentcc-provider"); got != "openai" {
					t.Errorf("x-agentcc-provider = %q, want openai", got)
				}
			}
			if got := up.models(); strings.Join(got, ",") != strings.Join(wantSeen, ",") {
				t.Errorf("upstream saw models %v, want %v", got, wantSeen)
			}
		})
	}
}

// TestChatEnforcesKeyProviderACL drives the per-key provider ACL
// (allowed_providers) through the real handler. A BYOK key in an org that has
// opted into managed models calls a managed model, which the managed
// provider "openrouter" serves. peekKeyOrgID records the key's ACL before
// routing, so resolveRoute's provider-ACL exit
// refuses the call before the provider is called.
func TestChatEnforcesKeyProviderACL(t *testing.T) {
	for _, tc := range []struct {
		name      string
		allowed   []string
		wantCode  int
		wantCalls int
	}{
		// Control: the setup reaches the upstream when the ACL allows it.
		{name: "allowed provider is served", allowed: []string{"openrouter"}, wantCode: http.StatusOK, wantCalls: 1},
		{name: "other provider is refused", allowed: []string{"anthropic"}, wantCode: http.StatusForbidden, wantCalls: 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := startModelRecorder(t)
			srv := newACLTestServer(t, up.URL, tc.allowed)
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
				strings.NewReader(`{"model":"vendor/acl-model","messages":[{"role":"user","content":"hi"}]}`))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer sk-route-acl")
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)

			calls := len(up.models())
			if w.Code != tc.wantCode || calls != tc.wantCalls {
				t.Fatalf("status = %d, upstream calls = %d; want %d and %d; body %s", w.Code, calls, tc.wantCode, tc.wantCalls, w.Body.String())
			}
			if tc.wantCode == http.StatusForbidden && !strings.Contains(w.Body.String(), "API key does not have access to provider: openrouter") {
				t.Errorf("body = %s, want the provider ACL refusal", w.Body.String())
			}
		})
	}
}

// newACLTestServer serves one managed model, "vendor/acl-model", through the
// managed provider "openrouter", to org "org-acl" (opted in, no credit
// requirement), whose BYOK key sk-route-acl may use only the allowed providers.
func newACLTestServer(t *testing.T, upstreamURL string, allowed []string) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "acl", Key: "sk-route-acl", Owner: "t", KeyType: "byok",
		Providers: allowed, Metadata: map[string]string{"org_id": "org-acl"},
	}}
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", FetchedAt: time.Now(), Models: map[string]*catalog.Model{
		"vendor/acl-model": {ID: "vendor/acl-model", OutputModalities: []string{"text"}, InputPerToken: 1e-6, OutputPerToken: 1e-6},
	}})
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-acl", &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: creditsplugin.NewOrgLedger(nil)})
	srv.ready.Store(true)
	return srv
}

// TestResolveRouteKeepsTheACLBranch pins resolveRoute's provider-ACL exit,
// with its status and message, by presetting what the auth plugin would
// record and calling resolveRoute directly. Enforcement through the handler
// is TestChatEnforcesKeyProviderACL's: ChatCompletion records the key's
// provider ACL before routing and reaches this branch.
func TestResolveRouteKeepsTheACLBranch(t *testing.T) {
	up := startModelRecorder(t)
	srv := createTestServer(t, up.URL)
	for _, tc := range []struct {
		allowed    string
		wantStatus int
	}{
		{allowed: "anthropic", wantStatus: http.StatusForbidden},
		{allowed: "anthropic,openai", wantStatus: 0},
	} {
		rc := models.AcquireRequestContext()
		req := &models.ChatCompletionRequest{Model: "gpt-4o"}
		rc.Model, rc.Request = req.Model, req
		rc.Metadata["auth_allowed_providers"] = tc.allowed
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
		p, _, err := srv.handlers.resolveRoute(r.Context(), r, rc, nil, "chat")
		rc.Release()
		if tc.wantStatus == 0 {
			if err != nil || p == nil || p.ID() != "openai" {
				t.Fatalf("allowed %q: provider %v, err %v; want openai", tc.allowed, p, err)
			}
			continue
		}
		apiErr, ok := err.(*models.APIError)
		if !ok || apiErr.Status != tc.wantStatus || apiErr.Message != "API key does not have access to provider: openai" {
			t.Fatalf("allowed %q: err = %v, want 403 for provider openai", tc.allowed, err)
		}
	}
}

// TestRouteStubsRegistered checks that New registers every routes_<concern>.go
// file exactly once, in the documented order, and that the routes in
// those files answer.
func TestRouteStubsRegistered(t *testing.T) {
	want := []string{
		"catalog_admin", "credits_admin", "capabilities", "models", "generation", "account",
		"key", "feedback", "catalog_access", "replay", "spendguard", "runtime", "mcp", "capability_echo",
	}
	var names []string
	for _, rs := range routeStubs {
		names = append(names, rs.name)
	}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("route stubs = %v, want %v", names, want)
	}

	real := routeStubs
	t.Cleanup(func() { routeStubs = real })
	var calls []string
	routeStubs = make([]routeStub, len(real))
	for i, rs := range real {
		routeStubs[i] = routeStub{rs.name, func(s *Server, router *Router) {
			calls = append(calls, rs.name)
			rs.register(s, router)
		}}
	}

	up := startModelRecorder(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "seams-admin"
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	if strings.Join(calls, ",") != strings.Join(want, ",") {
		t.Fatalf("New registered %v, want each of %v once, in order", calls, want)
	}

	for _, tc := range []struct {
		method, path string
		wantStatus   int
		wantInBody   string
	}{
		{"GET", "/v1/models", 200, `"id":"gpt-4o"`},
		{"GET", "/v1/models/gpt-4o", 200, `"id":"gpt-4o"`},
		{"GET", "/-/catalog", 404, `"code":"managed_models_disabled"`},
		{"GET", "/-/orgs/org-1/credits", 404, `"code":"managed_models_disabled"`},
		{"POST", "/-/orgs/org-1/credits", 404, `"code":"managed_models_disabled"`},
		{"PUT", "/-/orgs/org-1/credits", 404, `"code":"managed_models_disabled"`},
		{"DELETE", "/-/orgs/org-1/credits", 405, `"code":"method_not_allowed"`},
	} {
		req := httptest.NewRequest(tc.method, tc.path, nil)
		req.Header.Set("Authorization", "Bearer seams-admin")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != tc.wantStatus || !strings.Contains(w.Body.String(), tc.wantInBody) {
			t.Errorf("%s %s = %d %s; want %d with %s", tc.method, tc.path, w.Code, w.Body.String(), tc.wantStatus, tc.wantInBody)
		}
	}
}
