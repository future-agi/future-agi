package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func newOrgProviderLockServer(t *testing.T, global config.ProviderLockConfig, orgRouting *tenant.RoutingConfig) (*Server, *modelRecorder) {
	t.Helper()
	upstream := &modelRecorder{}
	mock := httptest.NewServer(upstream)
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org-a", Key: "sk-agentcc-lock-a", Owner: "user-a", KeyType: "byok", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "org-b", Key: "sk-agentcc-lock-b", Owner: "user-b", KeyType: "byok", Metadata: map[string]string{"org_id": "org-b"}},
	}
	remote := false // Skip loopback provider discovery probes.
	cfg.Providers = map[string]config.ProviderConfig{
		"openai": {BaseURL: mock.URL, APIKey: "test-platform-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Local: &remote},
	}
	cfg.Routing.ProviderLock = global
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })

	tenants := tenant.NewStore()
	for _, orgID := range []string{"org-a", "org-b"} {
		org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "test-" + orgID + "-key", Models: []string{"gpt-4o"}},
		}}
		if orgID == "org-a" {
			org.Routing = orgRouting
		}
		tenants.Set(orgID, org)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	t.Cleanup(func() { srv.Shutdown(context.Background()) })
	return srv, upstream
}

func postOrgProviderLock(t *testing.T, srv *Server, key, source, target string, stream bool) *httptest.ResponseRecorder {
	t.Helper()
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}],"stream":false}`
	if stream {
		body = strings.Replace(body, `"stream":false`, `"stream":true`, 1)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	if target != "" {
		if source == "metadata" {
			metadata, err := json.Marshal(map[string]string{"provider_lock": target})
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("x-agentcc-metadata", string(metadata))
		} else {
			req.Header.Set("x-agentcc-provider-lock", target)
		}
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func TestChatCompletion_OrgProviderLock(t *testing.T) {
	globalDeny := config.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openai"}}
	orgDeny := &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openai"}}}
	const denyMessage = `provider "openai" is in the deny list and cannot be locked to`
	tests := []struct {
		name        string
		global      config.ProviderLockConfig
		orgRouting  *tenant.RoutingConfig
		target      string
		stream      bool
		wantStatus  int
		wantMessage string
		wantLocked  string
	}{
		{
			name: "org deny list rejects the reported request", orgRouting: orgDeny,
			target: "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name: "org deny list rejects streaming before dispatch", orgRouting: orgDeny, stream: true,
			target: "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name: "omitted routing inherits global deny", global: globalDeny,
			target: "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name: "omitted provider lock inherits global deny", global: globalDeny, orgRouting: &tenant.RoutingConfig{},
			target: "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name: "omitted provider lock inherits global default", orgRouting: &tenant.RoutingConfig{},
			target: "openai", wantStatus: http.StatusOK, wantLocked: "openai",
		},
		{
			name: "explicit empty disable ignores the lock", orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: false}},
			target: "openai", wantStatus: http.StatusOK,
		},
		{
			name: "explicit empty disable overrides global deny", global: globalDeny,
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: false}},
			target:     "openai", wantStatus: http.StatusOK,
		},
		{
			name: "disabled org lists are inactive", global: globalDeny,
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: false, DenyProviders: []string{"openai"}}},
			target:     "openai", wantStatus: http.StatusOK,
		},
		{
			name:       "org allow list rejects unlisted provider",
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"other"}}},
			target:     "openai", wantStatus: http.StatusForbidden, wantMessage: `provider "openai" is not in the allowed providers list`,
		},
		{
			name: "org allow list replaces global deny", global: globalDeny,
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"openai"}}},
			target:     "openai", wantStatus: http.StatusOK, wantLocked: "openai",
		},
		{
			name: "empty enabled org policy replaces global deny", global: globalDeny,
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true}},
			target:     "openai", wantStatus: http.StatusOK, wantLocked: "openai",
		},
		{
			name:       "deny wins over allow within org policy",
			orgRouting: &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, AllowedProviders: []string{"openai"}, DenyProviders: []string{"openai"}}},
			target:     "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name:   "enabled org policy overrides disabled global policy",
			global: config.ProviderLockConfig{Enabled: false, DenyProviders: []string{"openai"}}, orgRouting: orgDeny,
			target: "openai", wantStatus: http.StatusForbidden, wantMessage: denyMessage,
		},
		{
			name:   "omitted org policy inherits disabled global policy",
			global: config.ProviderLockConfig{Enabled: false, DenyProviders: []string{"openai"}},
			target: "openai", wantStatus: http.StatusOK,
		},
		{
			name: "no lock leaves normal routing unchanged", orgRouting: orgDeny,
			wantStatus: http.StatusOK,
		},
	}
	for _, tt := range tests {
		for _, source := range []string{"header", "metadata"} {
			t.Run(tt.name+"/"+source, func(t *testing.T) {
				srv, upstream := newOrgProviderLockServer(t, tt.global, tt.orgRouting)
				w := postOrgProviderLock(t, srv, "sk-agentcc-lock-a", source, tt.target, tt.stream)
				if w.Code != tt.wantStatus {
					t.Errorf("status = %d, want %d: %s", w.Code, tt.wantStatus, w.Body.String())
				}
				if tt.wantMessage != "" {
					var response models.ErrorResponse
					if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
						t.Fatalf("decode error response: %v", err)
					}
					if response.Error.Message != tt.wantMessage {
						t.Errorf("message = %q, want %q", response.Error.Message, tt.wantMessage)
					}
				}
				if got := w.Header().Get("x-agentcc-provider-locked"); got != tt.wantLocked {
					t.Errorf("locked provider = %q, want %q", got, tt.wantLocked)
				}
				wantCalls := 0
				if tt.wantStatus == http.StatusOK {
					wantCalls = 1
				}
				if got := len(upstream.received()); got != wantCalls {
					t.Errorf("upstream calls = %d, want %d", got, wantCalls)
				}
			})
		}
	}
}

func TestChatCompletion_OrgProviderLockIsolation(t *testing.T) {
	for _, source := range []string{"header", "metadata"} {
		t.Run(source, func(t *testing.T) {
			srv, upstream := newOrgProviderLockServer(t, config.ProviderLockConfig{}, &tenant.RoutingConfig{
				ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openai"}},
			})
			// Reuse the same server in both directions: neither the resolver nor
			// a pooled request context may leak org A's policy into org B.
			for _, key := range []string{"sk-agentcc-lock-b", "sk-agentcc-lock-a", "sk-agentcc-lock-b", "sk-agentcc-lock-a", "sk-agentcc-lock-b"} {
				before := len(upstream.received())
				w := postOrgProviderLock(t, srv, key, source, "openai", false)
				wantStatus, wantCalls, wantLocked := http.StatusOK, 1, "openai"
				if key == "sk-agentcc-lock-a" {
					wantStatus, wantCalls, wantLocked = http.StatusForbidden, 0, ""
				}
				if w.Code != wantStatus {
					t.Errorf("%s: status = %d, want %d: %s", key, w.Code, wantStatus, w.Body.String())
				}
				if got := len(upstream.received()) - before; got != wantCalls {
					t.Errorf("%s: upstream calls = %d, want %d", key, got, wantCalls)
				}
				if got := w.Header().Get("x-agentcc-provider-locked"); got != wantLocked {
					t.Errorf("%s: locked provider = %q, want %q", key, got, wantLocked)
				}
			}
		})
	}
}
