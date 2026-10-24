package auth

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// KeyListLoaded reports whether a key list from the control plane has been
// applied: the readiness of a gateway whose control plane syncs keys waits
// for it. Keys pushed one by one (ImportKeys) or made here are not a list,
// and the store's own config keys never count.
func TestKeyListLoaded_OnlyOnceAKeyListIsApplied(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "ops", Key: "sk-config-key-001", KeyType: "internal"}}})
	if ks.KeyListLoaded() {
		t.Fatal("a store holding only its config keys reports a key list loaded")
	}

	ks.LoadFromHashes([]SyncedKey{{ID: "key-pushed", KeyHash: HashKey("sk-pushed-key-001")}})
	ks.Create("made-here", "o", nil, nil, nil)
	if ks.KeyListLoaded() {
		t.Fatal("keys pushed one by one or made here count as a loaded key list")
	}

	ks.SyncFromHashes([]SyncedKey{{ID: "key-listed", KeyHash: HashKey("sk-listed-key-001")}})
	if !ks.KeyListLoaded() {
		t.Fatal("a key list was applied, and the store does not report it loaded")
	}
	if ks.Authenticate("sk-listed-key-001") == nil {
		t.Fatal("the listed key does not authenticate once the list is reported loaded")
	}
}

// The control plane lists no key until the first is made: an empty first
// list is a loaded list too, or a new deployment would never be ready.
func TestKeyListLoaded_AnEmptyFirstListCounts(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{})
	ks.SyncFromHashes(nil)
	if !ks.KeyListLoaded() {
		t.Fatal("an empty first key list was applied, and the store does not report it loaded")
	}
}

// An empty list counts even when the store already holds a key of the sync
// source: a key Create made here (POST /-/keys) or one the app pushed one by
// one (POST /-/keys/sync) before the replica's first list. The empty-set
// guard keeps such a key (it never wipes every synced key on an empty list),
// and the list still counts as loaded: the app lists no key, so the
// replica's /readyz reports ready without waiting for one.
func TestKeyListLoaded_AnEmptyListCountsWhenTheStoreHoldsASyncedKey(t *testing.T) {
	for _, tc := range []struct {
		name string
		seed func(ks *KeyStore) string // returns the raw key it seeded
	}{
		{"a key made here", func(ks *KeyStore) string {
			_, raw := ks.Create("made-here", "o", nil, nil, nil)
			return raw
		}},
		{"a key pushed one by one", func(ks *KeyStore) string {
			ks.LoadFromHashes([]SyncedKey{{ID: "key-pushed", KeyHash: HashKey("sk-pushed-key-001")}})
			return "sk-pushed-key-001"
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ks := NewKeyStore(config.AuthConfig{})
			raw := tc.seed(ks)
			if ks.KeyListLoaded() {
				t.Fatal("set-up: the seeded key counts as a loaded key list")
			}

			ks.SyncFromHashes(nil)
			if !ks.KeyListLoaded() {
				t.Error("an empty key list was applied, and the store does not report it loaded")
			}
			if ks.Authenticate(raw) == nil {
				t.Error("the empty list dropped the seeded key: the guard must keep it")
			}

			ks.SyncFromHashes([]SyncedKey{})
			if !ks.KeyListLoaded() {
				t.Error("a second empty list cleared the loaded state")
			}
		})
	}
}
