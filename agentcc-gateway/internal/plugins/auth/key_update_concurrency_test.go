//go:build !race

// Built without -race. Update gives the key a new map and never writes into the one requests hold (TestUpdate_NeverWritesIntoTheMetadataMapReadersHold in internal/auth/keystore_test.go).

package auth

import (
	"context"
	"fmt"
	"sync"
	"testing"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Label updates on a key while requests use it: every request reads the key's
// own org, and every update lands.
func TestPlugin_KeyLabelUpdatesDuringRequestsKeepTheKeysOrg(t *testing.T) {
	ks := authpkg.NewKeyStore(config.AuthConfig{Enabled: true})
	key, raw := ks.Create("tenant-key", "tenant", nil, nil, map[string]string{"org_id": "org-a"})
	p := New(ks, true)

	deadline := time.Now().Add(2 * time.Second)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		for i := 0; time.Now().Before(deadline); i++ {
			label := map[string]string{"org_id": "org-a", fmt.Sprintf("label%d", i%64): "v"}
			// Results discarded: this test checks only what each request reads
			// from the key.
			_, _ = ks.Update(key.ID, nil, nil, nil, nil, label)
		}
	}()
	go func() {
		defer wg.Done()
		for time.Now().Before(deadline) {
			rc := models.AcquireRequestContext()
			rc.Metadata["authorization"] = "Bearer " + raw
			p.ProcessRequest(context.Background(), rc)
			if got := rc.Metadata["key_org_id"]; got != "org-a" {
				t.Errorf("key_org_id = %q, want org-a", got)
				rc.Release()
				return
			}
			rc.Release()
		}
	}()
	wg.Wait()

	// The updates must have gone through, or the test raced nothing.
	if got := ks.Get(key.ID).Metadata; got["label0"] != "v" || got["org_id"] != "org-a" {
		t.Fatalf("label updates never landed: %v", got)
	}
}
