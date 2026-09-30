package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// newOrgProviderURLTestServer builds a gateway whose config.yaml has an
// "openai" provider pointing at operatorURL, and whose one org ("org-urls")
// has the given providers. The org's key is sk-agentcc-org-urls.
func newOrgProviderURLTestServer(t *testing.T, operatorURL string, orgProviders map[string]*tenant.ProviderConfig) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name:     "org-user",
		Key:      "sk-agentcc-org-urls",
		Owner:    "user",
		KeyType:  "byok",
		Metadata: map[string]string{"org_id": "org-urls"},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   operatorURL,
		APIKey:    "operator-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set("org-urls", &tenant.OrgConfig{Providers: orgProviders})

	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	return srv
}

func postChat(t *testing.T, srv *Server, model string) (int, models.ErrorDetail) {
	t.Helper()
	body := `{"model":"` + model + `","messages":[{"role":"user","content":"hi"}]}`
	req := httptest.NewRequest("POST", "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer sk-agentcc-org-urls")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	var resp models.ErrorResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	return w.Code, resp.Error
}

// An org provider sharing its ID with a config.yaml entry must never send the
// org's key to the entry's base_url, including when the org's own base_url is
// refused.
func TestOrgProviderKeyNeverReachesConfigYAMLBaseURL(t *testing.T) {
	// Counts completions only: the registry's own connectivity probe of a
	// local provider also lands here.
	var operatorHits atomic.Int32
	operator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/chat/completions") {
			operatorHits.Add(1)
		}
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer operator.Close()
	orgUpstream := startMockOpenAI(t)
	defer orgUpstream.Close()

	srv := newOrgProviderURLTestServer(t, operator.URL, map[string]*tenant.ProviderConfig{
		"openai": {
			APIKey:    "org-key",
			BaseURL:   orgUpstream.URL, // 127.0.0.1: loopback, never allowed
			APIFormat: "openai",
			Models:    []string{"mock-gw"},
			Enabled:   true,
		},
	})

	status, apiErr := postChat(t, srv, "mock-gw")

	if got := operatorHits.Load(); got != 0 {
		t.Fatalf("config.yaml's openai upstream received %d request(s) for the org's model", got)
	}
	if status != http.StatusForbidden || apiErr.Code != "provider_base_url_blocked" {
		t.Fatalf("status = %d code = %q, want 403 provider_base_url_blocked; message: %s", status, apiErr.Code, apiErr.Message)
	}
	if !strings.Contains(apiErr.Message, "loopback") {
		t.Errorf("message = %q, want it to say the base_url is loopback", apiErr.Message)
	}
}

// A private base_url is refused with the reason and the opt-in, not the
// generic "not available for this API key". The RFC 6598 shared address space
// (100.64.0.0/10, CGNAT) counts as private: without the opt-in the request is
// refused, not proxied there.
func TestOrgProviderPrivateBaseURLRefusalExplainsOptIn(t *testing.T) {
	operator := startMockOpenAI(t)
	defer operator.Close()

	for _, addr := range []string{"10.255.255.1", "100.64.77.10"} {
		t.Run(addr, func(t *testing.T) {
			srv := newOrgProviderURLTestServer(t, operator.URL, map[string]*tenant.ProviderConfig{
				"custom": {
					APIKey:    "org-key",
					BaseURL:   "http://" + addr + ":8080",
					APIFormat: "openai",
					Models:    []string{"mock-custom"},
					Enabled:   true,
					Timeout:   1, // seconds: if it is ever proxied, fail fast
				},
			})

			status, apiErr := postChat(t, srv, "mock-custom")

			if status != http.StatusForbidden || apiErr.Code != "provider_base_url_blocked" {
				t.Fatalf("status = %d code = %q, want 403 provider_base_url_blocked; message: %s", status, apiErr.Code, apiErr.Message)
			}
			if !strings.Contains(apiErr.Message, config.EnvAllowPrivateProviderURLs+"=true") {
				t.Errorf("message = %q, want it to name %s", apiErr.Message, config.EnvAllowPrivateProviderURLs)
			}
			if strings.Contains(apiErr.Message, "not available for this API key") {
				t.Errorf("message = %q still blames the API key", apiErr.Message)
			}
			if strings.Contains(apiErr.Message, addr) {
				t.Errorf("message = %q echoes the resolved address", apiErr.Message)
			}
		})
	}
}

// An org provider the gateway cannot set up is a 502 server error, not a
// refusal: a base_url that is not usable says why, and any other build
// failure keeps its cause to the gateway log.
func TestOrgProviderBuildFailureIsA502ServerError(t *testing.T) {
	operator := startMockOpenAI(t)
	defer operator.Close()

	for _, tt := range []struct {
		name, baseURL, apiFormat, wantCode, wantMessage string
	}{
		{"unresolvable host", "http://no-such-host.invalid:8080", "openai", "provider_base_url_unusable", "does not resolve"},
		{"not an http URL", "ftp://files.example.com", "openai", "provider_base_url_unusable", "not a valid http(s) URL"},
		{"unsupported api_format", "http://203.0.113.7:8080", "no-such-format", "provider_unavailable", "check its configuration"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			srv := newOrgProviderURLTestServer(t, operator.URL, map[string]*tenant.ProviderConfig{
				"custom": {
					APIKey:    "org-key",
					BaseURL:   tt.baseURL,
					APIFormat: tt.apiFormat,
					Models:    []string{"mock-custom"},
					Enabled:   true,
				},
			})

			status, apiErr := postChat(t, srv, "mock-custom")

			if status != http.StatusBadGateway || apiErr.Code != tt.wantCode {
				t.Fatalf("status = %d code = %q, want 502 %s; message: %s", status, apiErr.Code, tt.wantCode, apiErr.Message)
			}
			if apiErr.Type != models.ErrTypeServer {
				t.Errorf("type = %q, want %q", apiErr.Type, models.ErrTypeServer)
			}
			if !strings.Contains(apiErr.Message, tt.wantMessage) {
				t.Errorf("message = %q, want it to say %q", apiErr.Message, tt.wantMessage)
			}
			if strings.Contains(apiErr.Message, "no-such-format") {
				t.Errorf("message = %q leaks the build error", apiErr.Message)
			}
		})
	}
}

// A model no org provider lists keeps the plain refusal: org keys are barred
// from config.yaml providers by design.
func TestUnlistedModelStillNotAvailableForOrgKey(t *testing.T) {
	operator := startMockOpenAI(t)
	defer operator.Close()

	srv := newOrgProviderURLTestServer(t, operator.URL, map[string]*tenant.ProviderConfig{
		"custom": {
			APIKey:  "org-key",
			BaseURL: "http://10.255.255.1:8080",
			Models:  []string{"mock-custom"},
			Enabled: true,
		},
	})

	status, apiErr := postChat(t, srv, "gpt-4o")

	if status != http.StatusForbidden || !strings.Contains(apiErr.Message, "not available for this API key") {
		t.Fatalf("status = %d message = %q, want 403 not available for this API key", status, apiErr.Message)
	}
}

// orgProviderBuildFailures counts the org provider build failures the gateway
// logs while fn runs.
func orgProviderBuildFailures(fn func()) int {
	var logs bytes.Buffer
	orig := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	fn()
	slog.SetDefault(orig)
	return strings.Count(logs.String(), "failed to create org provider for model")
}

// An org provider that lists the model but cannot be built is tried, and its
// failure logged, once per request.
func TestOrgProviderBuildFailureLoggedOncePerRequest(t *testing.T) {
	operator := startMockOpenAI(t)
	defer operator.Close()

	srv := newOrgProviderURLTestServer(t, operator.URL, map[string]*tenant.ProviderConfig{
		"custom": {
			APIKey:    "org-key",
			BaseURL:   "http://10.255.255.1:8080",
			APIFormat: "openai",
			Models:    []string{"mock-custom"},
			Enabled:   true,
		},
	})

	t.Run("chat completions", func(t *testing.T) {
		var status int
		var apiErr models.ErrorDetail
		logged := orgProviderBuildFailures(func() { status, apiErr = postChat(t, srv, "mock-custom") })

		if status != http.StatusForbidden || apiErr.Code != "provider_base_url_blocked" {
			t.Fatalf("status = %d code = %q, want 403 provider_base_url_blocked", status, apiErr.Code)
		}
		if logged != 1 {
			t.Errorf("build failure logged %d times, want once", logged)
		}
	})

	t.Run("anthropic messages", func(t *testing.T) {
		body := `{"model":"mock-custom","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}`
		req := httptest.NewRequest("POST", "/v1/messages", bytes.NewBufferString(body))
		req.Header.Set("Authorization", "Bearer sk-agentcc-org-urls")
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		logged := orgProviderBuildFailures(func() { srv.httpServer.Handler.ServeHTTP(w, req) })

		if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "private network address") {
			t.Fatalf("status = %d body = %s, want 403 explaining the private base_url", w.Code, w.Body.String())
		}
		if logged != 1 {
			t.Errorf("build failure logged %d times, want once", logged)
		}
	})

	t.Run("resolveProviderWithOrgFallback", func(t *testing.T) {
		rc := &models.RequestContext{Metadata: map[string]string{"key_type": "byok", tenant.MetadataKeyOrgID: "org-urls"}}
		orgID, orgCfg := srv.handlers.resolveOrgConfig(rc)
		var err error
		logged := orgProviderBuildFailures(func() {
			_, err = srv.handlers.resolveProviderWithOrgFallback(context.Background(), rc, orgID, orgCfg, "mock-custom")
		})

		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Code != "provider_base_url_blocked" {
			t.Fatalf("err = %v, want provider_base_url_blocked", err)
		}
		if logged != 1 {
			t.Errorf("build failure logged %d times, want once", logged)
		}
	})
}
