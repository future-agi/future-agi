package server

import (
	"bytes"
	"net/http/httptest"
	"testing"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	orgTimeoutOrgID = "org-timeouts"
	orgTimeoutKey   = "sk-agentcc-org-timeout-test"
)

// orgTimeoutKeys is one API key that belongs to orgTimeoutOrgID.
func orgTimeoutKeys() []config.AuthKeyConfig {
	return []config.AuthKeyConfig{{
		Name:     "org-user",
		Key:      orgTimeoutKey,
		Owner:    "user",
		KeyType:  "byok",
		Metadata: map[string]string{"org_id": orgTimeoutOrgID},
	}}
}

func TestResolveTimeout_OrgModelTimeout(t *testing.T) {
	const bearer = "Bearer " + orgTimeoutKey
	// config.yaml gives gpt-4o 120s and the server default is 60s.
	const fromConfigFile = 120 * time.Second

	tests := []struct {
		name        string
		orgTimeouts map[string]string // nil: the org has no routing config
		authHeader  string
		headers     map[string]string
		want        time.Duration
	}{
		{"the org's timeout for the model beats config.yaml", map[string]string{"gpt-4o": "45s"}, bearer, nil, 45 * time.Second},
		{"a request header beats the org's timeout", map[string]string{"gpt-4o": "45s"}, bearer, map[string]string{"x-agentcc-timeout": "30s"}, 30 * time.Second},
		{"the milliseconds header is honoured on this path", map[string]string{"gpt-4o": "45s"}, bearer, map[string]string{"x-agentcc-request-timeout": "5000"}, 5 * time.Second},
		{"a header that does not parse leaves the org's timeout", map[string]string{"gpt-4o": "45s"}, bearer, map[string]string{"x-agentcc-timeout": "30"}, 45 * time.Second},
		{"an org timeout for another model does not apply", map[string]string{"o1": "300s"}, bearer, nil, fromConfigFile},
		{"an org timeout that is not a duration is ignored", map[string]string{"gpt-4o": "soon"}, bearer, nil, fromConfigFile},
		{"a zero org timeout is ignored", map[string]string{"gpt-4o": "0s"}, bearer, nil, fromConfigFile},
		{"an org with no routing config falls through", nil, bearer, nil, fromConfigFile},
		{"a key that is not in the store gets no org timeout", map[string]string{"gpt-4o": "45s"}, "Bearer sk-agentcc-unknown", nil, fromConfigFile},
		{"a request with no key gets no org timeout", map[string]string{"gpt-4o": "45s"}, "", nil, fromConfigFile},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			orgCfg := &tenant.OrgConfig{}
			if tt.orgTimeouts != nil {
				orgCfg.Routing = &tenant.RoutingConfig{ModelTimeouts: tt.orgTimeouts}
			}
			tenantStore := tenant.NewStore()
			tenantStore.Set(orgTimeoutOrgID, orgCfg)

			h := &Handlers{
				defaultTimeout: 60 * time.Second,
				keyStore:       authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: orgTimeoutKeys()}),
				tenantStore:    tenantStore,
			}
			mt := map[string]time.Duration{"gpt-4o": fromConfigFile}
			h.modelTimeouts.Store(&mt)

			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = "gpt-4o"
			if tt.authHeader != "" {
				rc.Metadata["authorization"] = tt.authHeader
			}
			r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
			for k, v := range tt.headers {
				r.Header.Set(k, v)
			}

			if got := h.resolveTimeout(rc, r); got != tt.want {
				t.Errorf("resolveTimeout = %v, want %v", got, tt.want)
			}
			// The handler applies org overrides after this call; resolving
			// the timeout must not do that work early.
			if _, wrote := rc.Metadata[tenant.MetadataKeyOrgID]; wrote {
				t.Error("resolveTimeout wrote the org id to the request context")
			}
		})
	}
}

// The org's model timeout must reach a real request: a key that belongs to the
// org, through the whole handler, with config.yaml saying something else.
func TestChatCompletion_OrgModelTimeoutApplies(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = orgTimeoutKeys()
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIKey:    "platform-openai-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	cfg.Routing.ModelTimeouts = map[string]time.Duration{"gpt-4o": 90 * time.Second}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenantStore := tenant.NewStore()
	tenantStore.Set(orgTimeoutOrgID, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-openai-key", Models: []string{"gpt-4o"}},
		},
		Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"gpt-4o": "45s"}},
	})
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)

	appliedTimeoutMs := func(t *testing.T, headers map[string]string) string {
		t.Helper()
		body := `{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`
		req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
		req.Header.Set("Authorization", "Bearer "+orgTimeoutKey)
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 {
			t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
		}
		return w.Header().Get("x-agentcc-timeout-ms")
	}

	if got := appliedTimeoutMs(t, nil); got != "45000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 45000 (the org's timeout, not config.yaml's 90s)", got)
	}
	if got := appliedTimeoutMs(t, map[string]string{"x-agentcc-request-timeout": "5000"}); got != "5000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 5000 (the milliseconds header)", got)
	}
	if got := appliedTimeoutMs(t, map[string]string{"x-agentcc-timeout": "20s"}); got != "20000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 20000 (the duration header)", got)
	}
}
