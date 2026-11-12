package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestLicenseAuthDelegated733UsesOrdinaryKeyChecks(t *testing.T) {
	_, public := testRSAKeyPair(t)
	for _, tc := range []struct {
		name                  string
		delegated, configured bool
		want                  int
	}{
		{"verified principal", true, true, 204},
		{"network headers are not a principal", false, true, 401},
		{"configuration fails closed like a bearer key", true, false, 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.LicenseAuthConfig{Enabled: true}
			if tc.configured {
				cfg.PublicKey = public
			}
			handler := LicenseAuth(cfg, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if IsLicenseAuthorized(r.Context()) || GetLicenseClaims(r.Context()) != nil {
					t.Error("delegation elevated to license authorization")
				}
				w.WriteHeader(204)
			}))
			r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
			r.Header.Set(auth.EnvelopeHeader, "not-a-verified-envelope")
			if tc.delegated {
				r = r.WithContext(auth.WithDelegatedPrincipal(r.Context(), auth.EnvelopeClaims{Kind: "playground", Exp: time.Now().Add(time.Minute).Unix()}, "test-only-grant"))
			}
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("status=%d want=%d", w.Code, tc.want)
			}
		})
	}
}
