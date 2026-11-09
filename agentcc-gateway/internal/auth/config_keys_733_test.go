package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func Test733ConfigSeedsAreDeploymentIdentitiesOnly(t *testing.T) {
	for _, tc := range []struct {
		name, kind string
		metadata   map[string]string
		allowed    bool
	}{
		{"internal", "internal", nil, true},
		{"byok", "byok", nil, false},
		{"internal_org", "internal", map[string]string{"org_id": "org"}, false},
		{"internal_user", "internal", map[string]string{"user_id": "user"}, false},
		{"empty_org_is_still_person_metadata", "internal", map[string]string{"org_id": ""}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			raw := generateKey()
			cfg := config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: tc.name, Key: raw, KeyType: tc.kind, Metadata: tc.metadata}}}
			ks := NewKeyStore(cfg, true)
			if got := ks.Count() == 1; got != tc.allowed {
				t.Fatalf("loaded=%v want=%v", got, tc.allowed)
			}
			if ks.Authenticate(raw) != nil {
				t.Fatal("seed authenticated before list")
			}
			refused, _ := ks.ConfigKeysRefused()
			if !tc.allowed && refused != 1 {
				t.Fatalf("refused=%d", refused)
			}
			// A 733 answer (it carries the snapshot) always answers the
			// conflict check; one that does not must not release a seed. A
			// pre-733 answer is Test733DeployOrderNeverChangesReadinessOrKeys's.
			pull733(t, ks, map[string]interface{}{"status": true, "result": []interface{}{}, "snapshot": map[string]interface{}{"complete": true, "issued_ever": 1}})
			if tc.allowed && !ks.ConfigKeyPending(raw) {
				t.Fatal("a 733 answer without config_conflicts released a seed")
			}
			pull733(t, ks, map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{}})
			if got := ks.Authenticate(raw) != nil; got != tc.allowed {
				t.Fatalf("after list authenticated=%v", got)
			}
			// Standalone preserves the operator's configured credentials.
			if NewKeyStore(cfg).Authenticate(raw) == nil {
				t.Fatal("standalone behavior changed")
			}
		})
	}
}

func Test733DeletedHashConflictSurvivesEveryRestart(t *testing.T) {
	raw := generateKey()
	hash := HashKey(raw)
	for restart := 0; restart < 2; restart++ {
		ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw, KeyType: "internal"}}}, true)
		if !ks.ConfigKeyPending(raw) {
			t.Fatal("restart seed did not await list")
		}
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-Agentcc-Config-Key-Hashes") != hash {
				t.Error("seed hash not challenged")
			}
			json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{hash}, "snapshot": map[string]interface{}{"complete": true, "issued_ever": 1}})
		}))
		err := SyncKeysFromControlPlane(context.Background(), srv.URL, "", ks)
		srv.Close()
		if err != nil {
			t.Fatal(err)
		}
		if !ks.KeyListLoaded() || ks.Authenticate(raw) != nil || ks.ConfigKeyPending(raw) {
			t.Fatal("conflicting seed not permanently refused")
		}
		if ks.LoadFromHashes([]SyncedKey{{ID: "collision", KeyHash: hash, Metadata: map[string]string{"org_id": "org", "user_id": "user"}}}) != 0 {
			t.Fatal("collision revived through push")
		}
		n, reasons := ks.ConfigKeysRefused()
		if n != 1 || reasons["hash_collision"] != 1 {
			t.Fatalf("counts=%d %v", n, reasons)
		}
	}
}

func Test733ConfigSeedHeaderBound(t *testing.T) {
	cfg := config.AuthConfig{}
	for i := 0; i < 33; i++ {
		cfg.Keys = append(cfg.Keys, config.AuthKeyConfig{Key: generateKey(), KeyType: "internal"})
	}
	ks := NewKeyStore(cfg, true)
	if ks.Count() != 32 || len(ks.ConfigKeyHashes()) != 32 {
		t.Fatal("seed limit was not enforced")
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hashes := strings.Split(r.Header.Get("X-Agentcc-Config-Key-Hashes"), ",")
		if len(hashes) != 32 {
			t.Errorf("header values=%d", len(hashes))
		}
		for _, hash := range hashes {
			if !isSHA256Hex(hash) {
				t.Error("invalid header hash")
			}
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": []interface{}{}, "config_conflicts": []string{}})
	}))
	defer srv.Close()
	if err := SyncKeysFromControlPlane(context.Background(), srv.URL, "", ks); err != nil {
		t.Fatal(err)
	}
}

func Test733ConfigAndPositiveCollisionDropsBoth(t *testing.T) {
	for _, push := range []bool{false, true} {
		raw := generateKey()
		ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw, KeyType: "internal"}}}, true)
		keys := []SyncedKey{{ID: "person", KeyHash: HashKey(raw), Metadata: map[string]string{"org_id": "org", "user_id": "user"}}}
		if push {
			ks.LoadFromHashes(keys)
		} else {
			ks.SyncFromHashes(keys)
		}
		if ks.Count() != 0 || ks.Authenticate(raw) != nil {
			t.Fatal("a config-positive collision survived")
		}
	}
}

func Test733CreateRequiresPersonAndReservesUser(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{}, true)
	for _, meta := range []map[string]string{nil, {"org_id": "org"}, {"user_id": "user"}, {"org_id": "org", "user_id": " "}} {
		if key, raw := ks.Create("no-person", "", nil, nil, meta); key != nil || raw != "" {
			t.Fatal("created an unowned key")
		}
	}
	key, _ := ks.Create("person", "", nil, nil, map[string]string{"org_id": "org", "user_id": "user"})
	if key == nil {
		t.Fatal("valid person refused")
	}
	if _, err := ks.Update(key.ID, nil, nil, nil, nil, map[string]string{"user_id": "other"}); err == nil {
		t.Fatal("user authority changed")
	}
}
