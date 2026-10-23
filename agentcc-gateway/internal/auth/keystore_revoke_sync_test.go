package auth

import "testing"

// Django revokes a key on the gateway and then drops it from the key set. A
// periodic sync that read the set before the revoke and lands after it keeps
// the key revoked.
func TestSyncFromHashes_KeepsARevokedKeyRevoked(t *testing.T) {
	ks := NewKeyStore(authCfg())
	stale := []SyncedKey{
		{ID: "key_deleted", Name: "deleted", KeyHash: HashKey("sk-agentcc-deleted"), Metadata: map[string]string{"org_id": "org-a"}},
		{ID: "key_kept", Name: "kept", KeyHash: HashKey("sk-agentcc-kept"), Metadata: map[string]string{"org_id": "org-a"}},
	}
	if loaded := ks.SyncFromHashes(stale); loaded != 2 {
		t.Fatalf("loaded %d keys, want 2", loaded)
	}
	if !ks.Revoke("key_deleted") {
		t.Fatal("revoke: key not found")
	}

	ks.SyncFromHashes(stale)

	if ks.Authenticate("sk-agentcc-deleted") != nil {
		t.Error("a sync that still listed the revoked key made it authenticate again")
	}
	if got := ks.Get("key_deleted"); got == nil || got.Status != "revoked" {
		t.Errorf("revoked key after sync: %+v, want status revoked", got)
	}
	if ks.Authenticate("sk-agentcc-kept") == nil {
		t.Error("the key that was not revoked stopped authenticating")
	}

	// A sync from a set that omits it drops it.
	ks.SyncFromHashes(stale[1:])
	if ks.Get("key_deleted") != nil {
		t.Error("a key the set omits is still held")
	}
}
