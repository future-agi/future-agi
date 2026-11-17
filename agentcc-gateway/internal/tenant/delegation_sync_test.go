package tenant

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A slow org-config endpoint must not consume the delegation fetch's tick or
// deadline. Only the local stub is contacted; no live control plane is used.
func TestDelegated733SyncPrecedesSlowOrgConfig(t *testing.T) {
	order := make(chan string, 4)
	release := make(chan struct{})
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		order <- r.URL.Path
		if r.URL.Path == "/agentcc/org-configs/bulk/" {
			select {
			case <-release:
			case <-r.Context().Done():
			}
			fmt.Fprint(w, `{"status":true,"result":{}}`)
			return
		}
		fmt.Fprint(w, `{"status":true,"result":[],"delegation":{"complete":true,"fetched_at":"2026-10-28T22:00:00Z","members":[],"revoked_grants":[]}}`)
	}))
	defer control.Close()
	defer close(release)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		StartPeriodicSync(ctx, 20*time.Millisecond, control.URL, "test-only-admin", NewStore(), auth.NewKeyStore(config.AuthConfig{}))
	}()
	select {
	case path := <-order:
		if path != "/agentcc/api-keys/bulk/" {
			t.Errorf("delegation queued behind %s", path)
		}
	case <-time.After(5 * time.Second):
		t.Error("no periodic sync")
	}
	cancel()
	select {
	case <-stopped:
	case <-time.After(5 * time.Second):
		t.Error("periodic sync did not stop")
	}
}
