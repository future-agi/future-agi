//go:build !race

// The race detector reports the key.Metadata pointer swap in KeyStore.Update
// against this plugin's unlocked read of it. That swap cannot abort the
// process; what this test catches, without -race, is the in-place map write
// that did: "fatal error: concurrent map iteration and map write".

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

// A tenant may PATCH its key's labels while it sends traffic with the key.
// Each request copies the key's metadata in this plugin while Update merges
// the new labels in. That must never take the shared gateway down.
func TestPlugin_KeyLabelUpdateDuringRequestsDoesNotCrashTheProcess(t *testing.T) {
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
			// Results discarded so this file also builds against the store's
			// older (key, ok) signature, where it shows the abort.
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
