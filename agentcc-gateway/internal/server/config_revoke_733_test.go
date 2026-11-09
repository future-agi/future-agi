package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func Test733ConfigRevokeHTTPIsControlPlaneOnly(t *testing.T) {
	for _, controlPlane := range []bool{false, true} {
		name := "standalone"
		if controlPlane {
			name = "control_plane"
		}
		t.Run(name, func(t *testing.T) {
			const raw = "operator-config-revocation-test-only"
			ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw, KeyType: "internal"}}}, controlPlane)
			if controlPlane {
				app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{}})
				}))
				defer app.Close()
				if err := auth.SyncKeysFromControlPlane(context.Background(), app.URL, "", ks); err != nil {
					t.Fatal(err)
				}
			}
			key := ks.Authenticate(raw)
			if key == nil {
				t.Fatal("configured key did not authenticate before revoke")
			}
			handlers := NewKeyHandlers(ks, "admin-test-only")
			r := httptest.NewRequest(http.MethodDelete, "/-/keys?key_id="+key.ID, nil)
			setAdminAuth(r, "admin-test-only")
			w := httptest.NewRecorder()
			handlers.RevokeKey(w, r)
			if controlPlane {
				if w.Code != http.StatusConflict || !strings.Contains(w.Body.String(), "config_key_not_revocable") {
					t.Fatalf("control plane revoke: %d %s", w.Code, w.Body.String())
				}
			} else if w.Code != http.StatusOK {
				t.Fatalf("standalone revoke: %d %s", w.Code, w.Body.String())
			}
			if active := ks.Authenticate(raw) != nil; active != controlPlane {
				t.Fatalf("active after HTTP revoke=%v, control_plane=%v", active, controlPlane)
			}
		})
	}
}
