package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// TH-8084 review C11: LicenseAuth guards Future AGI managed models (Turing,
// Protect) with a managed-service token. It is not a Community cap: a
// self-hosted request that carries no licence token at all must reach the
// next middleware, whether the middleware is off (the shipped default) or on.
func TestLicenseAuthDoesNotGateCommunityRequests(t *testing.T) {
	_, publicPEM := testRSAKeyPair(t)
	for _, tc := range []struct {
		name   string
		cfg    config.LicenseAuthConfig
		method string
		path   string
		body   string
		bearer string
	}{
		{"default config, chat with gateway key", config.LicenseAuthConfig{}, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o"}`, "sk-agentcc-community"},
		{"default config, chat without any token", config.LicenseAuthConfig{}, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o"}`, ""},
		{"enabled, chat with gateway key", config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o"}`, "sk-agentcc-community"},
		{"enabled, embeddings without any token", config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, http.MethodPost, "/v1/embeddings", `{"model":"text-embedding-3-small"}`, ""},
		{"enabled, list models without any token", config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, http.MethodGet, "/v1/models", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			called := false
			handler := LicenseAuth(tc.cfg, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				called = true
				if IsLicenseAuthorized(r.Context()) {
					t.Fatal("a Community request must not be marked license-authorized")
				}
				w.WriteHeader(http.StatusNoContent)
			}))
			req := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			if tc.bearer != "" {
				req.Header.Set("Authorization", "Bearer "+tc.bearer)
			}
			rr := httptest.NewRecorder()
			handler.ServeHTTP(rr, req)
			if !called || rr.Code != http.StatusNoContent {
				t.Fatalf("Community request rejected by LicenseAuth: called=%v status=%d", called, rr.Code)
			}
		})
	}
}
