package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A restarted gateway pointed at the control plane gets every active key back
// under the ID Django stores it by, next to its config.yaml keys.
func TestSyncKeysFromControlPlane_RestoresKeysAfterRestart(t *testing.T) {
	var gotAuth string
	controlPlane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/api-keys/bulk/" {
			http.NotFound(w, r)
			return
		}
		gotAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(map[string]any{
			"status": true,
			"result": []map[string]any{
				{"id": "key_2", "name": "ui-key", "key_hash": HashKey("sk-agentcc-ui-key"), "key_prefix": "sk-agentcc-u...",
					"metadata": map[string]string{"org_id": "org-1"}},
				{"id": "key_5c0ffee", "name": "newer", "key_hash": HashKey("sk-agentcc-newer")},
			},
		})
	}))
	defer controlPlane.Close()

	// Fresh process: only the env-seeded internal key, which takes key_1.
	ks := NewKeyStore(authCfg(config.AuthKeyConfig{Name: "internal-backend", Key: "sk-agentcc-internal", KeyType: "internal"}))

	if err := SyncKeysFromControlPlane(context.Background(), controlPlane.URL, "shared-admin-token", ks); err != nil {
		t.Fatalf("SyncKeysFromControlPlane: %v", err)
	}

	if gotAuth != "Bearer shared-admin-token" {
		t.Errorf("Authorization = %q, want the control-plane token", gotAuth)
	}
	if k := ks.Authenticate("sk-agentcc-ui-key"); k == nil || k.ID != "key_2" || k.Metadata["org_id"] != "org-1" {
		t.Fatalf("UI key after restart = %+v, want key_2 for org-1", k)
	}
	if k := ks.Authenticate("sk-agentcc-newer"); k == nil || k.ID != "key_5c0ffee" {
		t.Fatalf("second key after restart = %+v", k)
	}
	if k := ks.Authenticate("sk-agentcc-internal"); k == nil || k.ID != "key_1" {
		t.Fatalf("internal key after sync = %+v, want untouched key_1", k)
	}
}

// A pulled key goes through the checks a pushed one does: one without an ID, a
// hex SHA-256 key_hash or a readable expires_at is left out, and the rest of
// the set still loads. A field the gateway does not know is ignored, so a newer
// control plane cannot empty an older gateway's store.
func TestSyncKeysFromControlPlane_LeavesOutMalformedKeys(t *testing.T) {
	for name, malformed := range map[string]map[string]any{
		"missing id":     {"name": "no-id", "key_hash": HashKey("sk-agentcc-no-id")},
		"raw key":        {"id": "key_raw", "key_hash": "sk-agentcc-not-a-hash"},
		"bad expires_at": {"id": "key_expiry", "key_hash": HashKey("sk-agentcc-expiry"), "expires_at": "next week"},
	} {
		t.Run(name, func(t *testing.T) {
			controlPlane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				json.NewEncoder(w).Encode(map[string]any{
					"status": true,
					"result": []map[string]any{
						{"id": "key_ok", "key_hash": HashKey("sk-agentcc-ok"), "added_later": "x"},
						malformed,
					},
				})
			}))
			defer controlPlane.Close()
			ks := NewKeyStore(authCfg())

			if err := SyncKeysFromControlPlane(context.Background(), controlPlane.URL, "", ks); err != nil {
				t.Fatalf("SyncKeysFromControlPlane: %v", err)
			}

			if k := ks.Authenticate("sk-agentcc-ok"); k == nil || k.ID != "key_ok" {
				t.Fatalf("well-formed key = %+v, want key_ok loaded", k)
			}
			if n := ks.Count(); n != 1 {
				t.Errorf("Count() = %d, want only the well-formed key", n)
			}
		})
	}
}
