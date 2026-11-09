package futureagi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
)

// The signer must use KeyAuth's person identity, not a config seed's labels
// or a metadata value that diverged after admission. Exercise the real auth
// middleware, plugin and signed HTTP request, with only the FI backend mocked.
func Test746eGatewayServicePrincipalComesFromKeyIdentity(t *testing.T) {
	secret := checkServiceVectors(t)
	t.Setenv("AGENTCC_ADMIN_TOKEN", secret)
	for _, tc := range []struct {
		name, source, label string
		wantSigned          bool
	}{
		{"config-seed-is-not-a-person", "config", serviceUser, false},
		{"synced-person", "sync", serviceUser, true},
		{"metadata-cannot-change-person", "sync", serviceOrg, true},
		{"metadata-person-is-not-required", "sync", "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				fields, err := verifyServiceWire(secret, r.Header.Get(auth.EnvelopeHeader))
				if err != nil || len(fields) != 19 {
					t.Error("backend did not receive a verified envelope")
				} else if fields[13] != serviceUser {
					t.Error("envelope person came from metadata instead of KeyIdentity")
				}
				io.WriteString(w, `{"result":[{"evaluations":[{"output":"Passed"}]}]}`)
			}))
			defer stub.Close()
			t.Setenv("FI_BASE_URL", stub.URL)
			const credential = "test-only-746e-principal-credential"
			metadata := map[string]string{"org_id": serviceOrg, "user_id": serviceUser, "workspace_id": serviceWorkspace}
			ks := auth.NewKeyStore(config.AuthConfig{Enabled: true})
			if tc.source == "config" {
				ks = auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
					Key: credential, Name: "seed", Owner: serviceUser, Metadata: metadata,
				}}})
			} else if ks.LoadFromHashes([]auth.SyncedKey{{ID: "person-746e", KeyHash: auth.HashKey(credential), Metadata: metadata}}) != 1 {
				t.Fatal("person fixture was not loaded")
			}
			var admitted bool
			h := middleware.KeyAuth(ks, true, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				admitted = true
				id, ok := middleware.KeyIdentity(r.Context())
				if !ok || (id.UserID != "") != tc.wantSigned {
					t.Fatal("fixture has the wrong authenticated person identity")
				}
				rc := &models.RequestContext{Metadata: map[string]string{"authorization": r.Header.Get("Authorization")}}
				authplugin.New(ks, true).ProcessRequest(r.Context(), rc)
				rc.Metadata["key_user_id"] = tc.label
				input := serviceInput()
				input.Metadata = rc.Metadata
				g := New("principal", map[string]interface{}{"auth": "gateway_service", "eval_id": "15", "retry": 0}, nil)
				if got := g.Check(r.Context(), input); got.Pass != tc.wantSigned {
					t.Errorf("signed=%v, want %v: %s", got.Pass, tc.wantSigned, got.Message)
				}
			}))
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
			r.Header.Set("Authorization", "Bearer "+credential)
			h.ServeHTTP(httptest.NewRecorder(), r)
			if !admitted {
				t.Fatal("fixture never reached the guardrail")
			}
			wantCalls := int32(0)
			if tc.wantSigned {
				wantCalls = 1
			}
			if calls.Load() != wantCalls {
				t.Errorf("backend calls=%d, want %d", calls.Load(), wantCalls)
			}
		})
	}
}
