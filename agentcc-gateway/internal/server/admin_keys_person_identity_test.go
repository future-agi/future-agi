package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The admin-token transport is trusted issuance in both deployment modes;
// standalone config seeds are not. Pin the distinction at the HTTP boundary.
func TestAdminCreatePersonIdentityBothModes(t *testing.T) {
	const person = "01234567-89ab-4cde-8fab-0123456789ab"
	const body = `{"name":"person","owner":"display-only","metadata":{"org_id":"org-a","user_id":"` + person + `"}}`
	for _, controlPlane := range []bool{false, true} {
		t.Run(map[bool]string{false: "standalone", true: "control-plane"}[controlPlane], func(t *testing.T) {
			ks := auth.NewKeyStore(config.AuthConfig{}, controlPlane)
			h := NewKeyHandlers(ks, "synthetic-admin-fixture")
			for _, authorized := range []bool{false, true} {
				req := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(body))
				if authorized {
					setAdminAuth(req, "synthetic-admin-fixture")
				}
				w := httptest.NewRecorder()
				h.CreateKey(w, req)
				if !authorized {
					if w.Code != http.StatusUnauthorized || ks.Count() != 0 {
						t.Fatal("unauthorized request issued a person key")
					}
					continue
				}
				if w.Code != http.StatusCreated {
					t.Fatalf("authorized create status = %d", w.Code)
				}
				var created struct {
					ID  string `json:"id"`
					Key string `json:"key"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
					t.Fatal(err)
				}
				key := ks.Authenticate(created.Key)
				if key == nil {
					t.Fatal("issued key did not authenticate")
				}
				if got := ks.Identity(key); got != (auth.Identity{KeyID: created.ID, OrgID: "org-a", UserID: person}) {
					t.Fatal("admin-token issuance lost its reserved person identity")
				}
			}
		})
	}
}
