package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

type revocationPublisherSpy struct {
	ids []string
}

func (p *revocationPublisherSpy) Publish(_ context.Context, keyID string) {
	p.ids = append(p.ids, keyID)
}

// Exercise the real server route and SetKeyRevocationPublisher without Redis.
// An absent local key must still be broadcast: another replica may hold it.
func TestAdminDeleteKeyPublishesRevocation(t *testing.T) {
	for _, tc := range []struct {
		name        string
		keyID       string
		wantStatus  int
		wantCode    string
		wantPublish bool
	}{
		{"held_key", "synced-person-key", http.StatusOK, "", true},
		{"absent_key", "only-held-by-another-replica", http.StatusNotFound, "key_not_found", true},
		{"config_key", "key_1", http.StatusConflict, "config_key_not_revocable", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const adminToken = "synthetic-revocation-admin"
			const rawKey = "synthetic-revocation-person-key"
			cfg := config.DefaultConfig()
			cfg.Admin.Token = adminToken
			cfg.Auth = config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
				Name: "synthetic-deployment-key", Key: "synthetic-revocation-config-key", KeyType: "internal",
			}}}
			ks := auth.NewKeyStore(cfg.Auth, true)
			if ks.LoadFromHashes([]auth.SyncedKey{{
				ID: "synced-person-key", KeyHash: auth.HashKey(rawKey),
				Metadata: map[string]string{"org_id": "revocation-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"},
			}}) != 1 || ks.Authenticate(rawKey) == nil {
				t.Fatal("person key fixture must authenticate before DELETE")
			}
			if !ks.ConfigKeyNotRevocable("key_1") {
				t.Fatal("config fixture must exercise the control-plane 409 guard")
			}
			if tc.name == "absent_key" && ks.Get(tc.keyID) != nil {
				t.Fatal("absent key fixture unexpectedly exists locally")
			}

			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := srv.Shutdown(ctx); err != nil {
					t.Error(err)
				}
			})
			pub := &revocationPublisherSpy{}
			srv.SetKeyRevocationPublisher(pub)

			req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+tc.keyID, nil)
			req.Header.Set("Authorization", "Bearer "+adminToken)
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, req)
			if w.Code != tc.wantStatus {
				t.Fatalf("DELETE status = %d, want %d", w.Code, tc.wantStatus)
			}
			if tc.wantPublish {
				if len(pub.ids) != 1 || pub.ids[0] != tc.keyID {
					t.Errorf("Publish calls = %q, want exactly [%q]", pub.ids, tc.keyID)
				}
			} else if len(pub.ids) != 0 {
				t.Errorf("config-key 409 must not publish; calls = %q", pub.ids)
			}
			if tc.wantCode != "" {
				var body struct {
					Error struct {
						Code string `json:"code"`
					} `json:"error"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if body.Error.Code != tc.wantCode {
					t.Errorf("error code = %q, want %q", body.Error.Code, tc.wantCode)
				}
			}
			if got := ks.Authenticate(rawKey) != nil; got != (tc.name != "held_key") {
				t.Errorf("local person key authenticates = %v after %s", got, tc.name)
			}
			if key := ks.Get("key_1"); key == nil || !key.IsActive() {
				t.Error("deployment config key must remain active")
			}
		})
	}
}
