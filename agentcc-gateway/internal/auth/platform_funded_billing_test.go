package auth

import (
	"slices"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A key's billing is set only by the platform: billing is a reserved name,
// the key sync keeps it, and an update cannot set or change it.
func TestKeyBillingIsSetOnlyByThePlatform(t *testing.T) {
	if !IsReservedMetadataKey("billing") {
		t.Fatal("billing is not a reserved key metadata name")
	}
	if !slices.Contains(ReservedMetadataKeys(), "billing") {
		t.Fatal("ReservedMetadataKeys does not list billing")
	}
	if got := NotPlatformSetMetadata(map[string]string{"billing": "platform_funded"}); len(got) != 0 {
		t.Fatalf("the key sync would drop billing: %v", got)
	}

	ks := NewKeyStore(config.AuthConfig{})
	exp := time.Now().Add(time.Hour)
	added := ks.LoadFromHashes([]SyncedKey{{
		ID: "key-run", Name: "run", KeyHash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		Metadata:  map[string]string{"org_id": "org-1", "billing": "platform_funded"},
		ExpiresAt: &exp,
	}})
	if added != 1 {
		t.Fatalf("LoadFromHashes added %d keys, want 1", added)
	}
	key := ks.Get("key-run")
	if key == nil || key.Metadata["billing"] != "platform_funded" {
		t.Fatalf("the synced key lost its platform-set billing: %+v", key)
	}

	plain, _ := ks.Create("tenant", "", nil, nil, map[string]string{"org_id": "org-1"})
	if _, err := ks.Update(plain.ID, nil, nil, nil, nil, map[string]string{"org_id": "org-1", "billing": "platform_funded"}); err == nil {
		t.Fatal("an update set billing on a key")
	}
	if got := ks.Get(plain.ID).Metadata["billing"]; got != "" {
		t.Fatalf("a refused update left billing %q on the key", got)
	}
}
