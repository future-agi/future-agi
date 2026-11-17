package auth

import (
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestMCPReadIdentityIsReadOnlySnapshot(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "fixture", Key: "synthetic-snapshot-fixture", Metadata: map[string]string{"org_id": "org-a"}, AllowedTools: []string{"one"}}}})
	key := ks.Lookup("synthetic-snapshot-fixture")
	id, allowed, _, ok := ks.MCPReadIdentity(key.ID)
	if !ok || id.OrgID != "org-a" || len(allowed) != 1 {
		t.Fatal("missing identity snapshot")
	}
	allowed[0] = "mutated"
	_, again, _, _ := ks.MCPReadIdentity(key.ID)
	if again[0] != "one" || key.GetLastUsedAt() != nil {
		t.Fatal("preview changed key state")
	}
	if _, _, _, ok := ks.MCPReadIdentity("missing"); ok {
		t.Fatal("unknown key accepted")
	}
	ks.Revoke(key.ID)
	if _, _, _, ok := ks.MCPReadIdentity(key.ID); ok {
		t.Fatal("revoked key accepted")
	}
}

func TestMCPReadIdentityConcurrentUpdate(t *testing.T) {
	ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "fixture", Key: "synthetic-concurrent-fixture", Metadata: map[string]string{"org_id": "org-a"}}}})
	key := ks.Lookup("synthetic-concurrent-fixture")
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		for i := 0; i < 100; i++ {
			ks.MCPReadIdentity(key.ID)
		}
	}()
	go func() {
		defer wg.Done()
		for i := 0; i < 100; i++ {
			name := "renamed"
			_, _ = ks.Update(key.ID, &name, nil, nil, nil, nil)
		}
	}()
	wg.Wait()
}
