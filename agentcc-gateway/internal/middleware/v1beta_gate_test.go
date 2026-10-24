package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The native
// Google API under /v1beta is gated at the edge like /v1. KeyAuth, given the
// /v1beta root, authenticates the key the GenAI handler reads (?key=, then
// x-goog-api-key, then Bearer), and LicenseAuth verifies a managed service
// token there as it does on /v1 (such a token is scoped to chat completions,
// so on /v1beta it is refused before any handler runs).

const v1betaGateKey = "sk-agentcc-mw-v1beta-0001"

func v1betaKeyAuth(t *testing.T) (http.Handler, *bool) {
	t.Helper()
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Name: "g", Key: v1betaGateKey, KeyType: "byok", Metadata: map[string]string{"org_id": "org-g"}},
	}})
	called := new(bool)
	h := KeyAuth(ks, true, nil, "/v1beta")(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*called = true
		if id, ok := KeyIdentity(r.Context()); !ok || id.OrgID != "org-g" {
			t.Errorf("the handler ran without the key's identity: %+v %v", id, ok)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	return h, called
}

func TestKeyAuth_V1BetaReadsTheGoogleKeyForms(t *testing.T) {
	for _, tc := range []struct {
		name    string
		query   string
		headers map[string]string
		want    int
	}{
		{"x-goog-api-key", "", map[string]string{"x-goog-api-key": v1betaGateKey}, http.StatusNoContent},
		{"?key=", "?key=" + v1betaGateKey, nil, http.StatusNoContent},
		{"Bearer", "", map[string]string{"Authorization": "Bearer " + v1betaGateKey}, http.StatusNoContent},
		{"?key= before a wrong Bearer", "?key=" + v1betaGateKey, map[string]string{"Authorization": "Bearer sk-wrong"}, http.StatusNoContent},
		{"a wrong ?key= before a valid Bearer", "?key=sk-wrong", map[string]string{"Authorization": "Bearer " + v1betaGateKey}, http.StatusUnauthorized},
		{"a wrong x-goog-api-key before a valid Bearer", "", map[string]string{"x-goog-api-key": "sk-wrong", "Authorization": "Bearer " + v1betaGateKey}, http.StatusUnauthorized},
		{"x-api-key is not read on /v1beta", "", map[string]string{"x-api-key": v1betaGateKey}, http.StatusUnauthorized},
		{"Authorization without the Bearer scheme", "", map[string]string{"Authorization": v1betaGateKey}, http.StatusUnauthorized},
		{"no key", "", nil, http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h, called := v1betaKeyAuth(t)
			req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-x:generateContent"+tc.query, strings.NewReader(`{}`))
			for k, v := range tc.headers {
				req.Header.Set(k, v)
			}
			w := httptest.NewRecorder()
			h.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Fatalf("status %d, want %d; body %s", w.Code, tc.want, w.Body.String())
			}
			if *called != (tc.want == http.StatusNoContent) {
				t.Errorf("handler called = %v, want %v", *called, tc.want == http.StatusNoContent)
			}
		})
	}
}

// /v1 keeps its own key forms: Bearer, then x-api-key. A ?key= or an
// x-goog-api-key is not a /v1 key.
func TestKeyAuth_V1KeyForms(t *testing.T) {
	for _, tc := range []struct {
		name    string
		query   string
		headers map[string]string
		want    int
	}{
		{"Bearer", "", map[string]string{"Authorization": "Bearer " + v1betaGateKey}, http.StatusNoContent},
		{"x-api-key", "", map[string]string{"x-api-key": v1betaGateKey}, http.StatusNoContent},
		{"?key= is not a /v1 key", "?key=" + v1betaGateKey, nil, http.StatusUnauthorized},
		{"x-goog-api-key is not a /v1 key", "", map[string]string{"x-goog-api-key": v1betaGateKey}, http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h, _ := v1betaKeyAuth(t)
			req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions"+tc.query, strings.NewReader(`{}`))
			for k, v := range tc.headers {
				req.Header.Set(k, v)
			}
			w := httptest.NewRecorder()
			h.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Fatalf("status %d, want %d; body %s", w.Code, tc.want, w.Body.String())
			}
		})
	}
}

func TestLicenseAuth_V1BetaVerifiesAManagedToken(t *testing.T) {
	privateKey, publicPEM := testRSAKeyPair(t)
	token := signTestLicenseToken(t, privateKey, licenseClaims{
		LicenseID:  "lic_test",
		InstanceID: "inst_test",
		Scope:      "enterprise",
		Services:   []string{"turing"},
		Models:     []string{"turing_small"},
		ExpiresAt:  time.Now().Add(time.Hour).Unix(),
		JTI:        "tok_test",
	})
	for _, tc := range []struct {
		name, auth string
		want       int
	}{
		// A managed token is scoped to chat completions: refused here as on /v1/models.
		{"valid managed token", "Bearer " + token, http.StatusForbidden},
		{"forged managed token", "Bearer " + token[:len(token)-4] + "AAAA", http.StatusUnauthorized},
	} {
		t.Run(tc.name, func(t *testing.T) {
			handler := LicenseAuth(config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, nil)(
				http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					t.Errorf("the handler ran for a %s on /v1beta", tc.name)
					w.WriteHeader(http.StatusNoContent)
				}))
			req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-x:generateContent", strings.NewReader(`{"contents":[]}`))
			req.Header.Set("Authorization", tc.auth)
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, req)
			if w.Code != tc.want {
				t.Fatalf("status %d, want %d; body %s", w.Code, tc.want, w.Body.String())
			}
		})
	}
}

// A gateway key (not a JWT) and the Google key forms pass LicenseAuth on
// /v1beta untouched, for KeyAuth to check.
func TestLicenseAuth_V1BetaPassesGatewayKeysThrough(t *testing.T) {
	_, publicPEM := testRSAKeyPair(t)
	for _, headers := range []map[string]string{
		{"Authorization": "Bearer " + v1betaGateKey},
		{"x-goog-api-key": v1betaGateKey},
		{},
	} {
		called := false
		handler := LicenseAuth(config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, nil)(
			http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				called = true
				w.WriteHeader(http.StatusNoContent)
			}))
		req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-x:generateContent", strings.NewReader(`{}`))
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, req)
		if !called || w.Code != http.StatusNoContent {
			t.Errorf("headers %v: handler called %v, status %d; want passed through", headers, called, w.Code)
		}
	}
}
