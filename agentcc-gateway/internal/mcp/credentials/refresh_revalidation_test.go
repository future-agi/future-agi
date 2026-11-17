package credentials

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// The real token transport must send the non-refreshing check, rather than
// relying on a synthetic source to suppress a second refresh.
func TestPerUserLifecycleRefreshRevalidatesOverTokenChannel(t *testing.T) {
	for _, state := range []string{"current", "revoked", "evicted-again"} {
		t.Run(state, func(t *testing.T) {
			rig := newLifecycleRig(t)
			r := rig.resolver
			var refreshes, checks atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				var body map[string]any
				if json.NewDecoder(req.Body).Decode(&body) != nil {
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				if problems := schematest.ValidateDef(t, "mcp-upstream-token-v1.schema.json", "Request", body); len(problems) != 0 {
					t.Error("token request violated its contract")
				}
				if body["refresh"] == false {
					checks.Add(1)
					if state == "revoked" {
						_ = json.NewEncoder(w).Encode(map[string]any{"error": RefusalRevoked})
						return
					}
					if state == "evicted-again" {
						if _, err := r.PurgeCredentials(req.Context(), "org-a", "u1", "issues"); err != nil {
							t.Error(err)
						}
					}
				} else {
					refreshes.Add(1)
					if _, err := r.PurgeCredentials(req.Context(), "org-a", "u1", "issues"); err != nil {
						t.Error(err)
					}
				}
				_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "synthetic-short", "token_type": "Bearer", "expires_at": time.Now().Add(90 * time.Second).UTC().Format(time.RFC3339), "credential_version": 2})
			}))
			defer server.Close()
			client, err := NewTokenClient(TokenClientOptions{BaseURL: server.URL, Secret: testDjangoSecret})
			if err != nil {
				t.Fatal(err)
			}
			r.tokens = client
			auth, _, d := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			if refreshes.Load() != 1 || checks.Load() != 1 {
				t.Fatal("token transport did not bound refresh and revalidation")
			}
			if state == "current" {
				if d != nil || auth == nil || auth.Lifetime.Err() != nil {
					t.Fatal("current short-lived grant was refused")
				}
			} else {
				if d == nil || auth != nil {
					t.Fatal("revalidation published an invalidated grant")
				}
			}
		})
	}
}
