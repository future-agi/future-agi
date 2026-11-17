package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func pull733(t *testing.T, ks *KeyStore, body map[string]interface{}) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(body)
	}))
	defer srv.Close()
	if err := SyncKeysFromControlPlane(context.Background(), srv.URL, "", ks); err != nil {
		t.Fatal(err)
	}
}

func Test733AuthoritativeEmptyListExpiresUnlistedCreate(t *testing.T) {
	for _, tc := range []struct {
		name     string
		snapshot interface{}
		kept     bool
	}{
		{"authoritative", map[string]interface{}{"complete": true, "issued_ever": 1}, false},
		{"wrong_database", map[string]interface{}{"complete": true, "issued_ever": 0}, true},
		{"incomplete", map[string]interface{}{"complete": false, "issued_ever": 1}, true},
		{"legacy", nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ks := NewKeyStore(config.AuthConfig{})
			now := time.Now()
			ks.now = func() time.Time { return now }
			_, raw := ks.Create("unlisted", "person", nil, nil, map[string]string{"org_id": "org", "user_id": "user"})
			now = now.Add(config.DefaultKeySyncGrace + time.Second)
			body := map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{}}
			if tc.snapshot != nil {
				body["snapshot"] = tc.snapshot
			}
			pull733(t, ks, body)
			if got := ks.Authenticate(raw) != nil; got != tc.kept {
				t.Fatalf("kept = %v, want %v", got, tc.kept)
			}
		})
	}
}

func Test733NegativeSyncRevokesLastKeyAndCannotResurrect(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{})
	key, raw := ks.Create("last", "person", nil, nil, map[string]string{"org_id": "org", "user_id": "user"})
	retired := []map[string]string{{"id": key.ID, "key_hash": key.KeyHash}}
	pull733(t, ks, map[string]interface{}{"status": true, "result": []interface{}{}, "retired": retired})
	if ks.Authenticate(raw) != nil {
		t.Fatal("retired last key still authenticates")
	}
	// A later stale positive row must never undo retirement, including after an empty poll.
	pull733(t, ks, map[string]interface{}{"status": true, "result": []interface{}{}, "snapshot": map[string]interface{}{"complete": true, "issued_ever": 1}})
	ks.LoadFromHashes([]SyncedKey{{ID: key.ID, KeyHash: key.KeyHash}})
	if ks.Authenticate(raw) != nil {
		t.Fatal("retired hash resurrected by additive push")
	}
}
