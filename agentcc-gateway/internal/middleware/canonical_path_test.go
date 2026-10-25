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

// Spellings of /v1/videos/vid_1 that a path cleaner folds back into it. Every
// gate decides on the canonical path, so each spelling is gated exactly like
// /v1/videos/vid_1.
var v1Spellings = []string{
	"/v1/videos/vid_1",
	"//v1/videos/vid_1",
	"///v1/videos/vid_1",
	"/v1//videos/vid_1",
	"/v1/videos/vid_1/",
	"/./v1/videos/vid_1",
	"/v1/./videos/vid_1",
	"/zz/../v1/videos/vid_1",
	"/v1/zz/../videos/vid_1",
	"/%2Fv1/videos/vid_1",
}

func mustNotRun(t *testing.T) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("%s %s reached the handler", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusTeapot)
	})
}

func TestKeyAuthGatesEverySpellingOfAV1Path(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
		Name: "k", Key: "sk-canonical", Owner: "o", KeyType: "internal",
	}}})
	gate := KeyAuth(ks, true, nil)

	for _, target := range v1Spellings {
		t.Run(target, func(t *testing.T) {
			rr := httptest.NewRecorder()
			gate(mustNotRun(t)).ServeHTTP(rr, httptest.NewRequest(http.MethodGet, target, nil))
			if rr.Code != http.StatusUnauthorized {
				t.Fatalf("GET %s with no key: status %d, want 401", target, rr.Code)
			}
		})
	}

	// A valid key still passes on the canonical path.
	called := false
	ok := gate(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
	req := httptest.NewRequest(http.MethodGet, "/v1/videos/vid_1", nil)
	req.Header.Set("Authorization", "Bearer sk-canonical")
	ok.ServeHTTP(httptest.NewRecorder(), req)
	if !called {
		t.Fatal("a valid key on the canonical path did not reach the handler")
	}

	// Paths outside /v1 are not this gate's business.
	for _, target := range []string{"/healthz", "/-/keys", "/v1beta/models/x", "/v1"} {
		called = false
		ok.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, target, nil))
		if !called {
			t.Fatalf("GET %s was gated by KeyAuth; only /v1/ is", target)
		}
	}
}

func TestLicenseAuthGatesEverySpellingOfAV1Path(t *testing.T) {
	// Enabled with no verification key: every /v1/ request fails closed (503).
	gate := LicenseAuth(config.LicenseAuthConfig{Enabled: true}, nil)

	for _, target := range v1Spellings {
		t.Run(target, func(t *testing.T) {
			rr := httptest.NewRecorder()
			gate(mustNotRun(t)).ServeHTTP(rr, httptest.NewRequest(http.MethodGet, target, nil))
			if rr.Code != http.StatusServiceUnavailable {
				t.Fatalf("GET %s: status %d, want 503 (gate skipped the path)", target, rr.Code)
			}
		})
	}
}

// A managed-service token is scoped to exactly POST /v1/chat/completions. Any
// other spelling of that path must not come out license-authorized.
func TestLicenseAuthManagedTokenIsNotHonouredOnANonCanonicalSpelling(t *testing.T) {
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
	gate := LicenseAuth(config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, nil)

	for _, target := range []string{"//v1/chat/completions", "/v1/chat/completions/", "/./v1/chat/completions"} {
		t.Run(target, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, target, strings.NewReader(`{"model":"turing_small"}`))
			req.Header.Set("Authorization", "Bearer "+token)
			rr := httptest.NewRecorder()
			gate(mustNotRun(t)).ServeHTTP(rr, req)
			if rr.Code != http.StatusForbidden {
				t.Fatalf("POST %s with a managed token: status %d, want 403", target, rr.Code)
			}
		})
	}
}
