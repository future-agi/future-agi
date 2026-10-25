package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// With bundled datastores the Helm chart's bootstrap job (migrations) is a
// post-install hook, and `helm install --wait` runs it only once every
// workload, the gateway included, reports ready. Until the job has run, the
// app's key list answers 503 migrations_pending: its database has no key
// table, so it has made no key. /readyz counts that answer as the app's
// (empty) key list while no list has loaded, so the install can go on; any
// other failed answer keeps the gateway out of rotation.
func TestReadyz_ReadyWhileTheAppHasNoKeyTable(t *testing.T) {
	var answer atomic.Value // "pending", "down" or "list"
	answer.Store("down")
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch answer.Load() {
		case "pending":
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
		case "down":
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"result":"starting"}`)
		default:
			fmt.Fprintf(w, `{"status":true,"result":[{"id":"key-1","name":"k","key_hash":%q}]}`, auth.HashKey("sk-synced-1"))
		}
	}))
	defer app.Close()

	cfg := config.DefaultConfig()
	cfg.ControlPlane = config.ControlPlaneConfig{URL: app.URL, SyncOnStartup: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ks := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true) // Start has run
	readyz := func() int {
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
		return w.Code
	}
	sync := func(a string) {
		answer.Store(a)
		_ = auth.SyncKeysFromControlPlane(context.Background(), app.URL, "t", ks)
	}

	sync("down")
	if got := readyz(); got != http.StatusServiceUnavailable {
		t.Fatalf("/readyz while the app cannot list its keys = %d, want 503", got)
	}
	sync("pending")
	if got := readyz(); got != http.StatusOK {
		t.Fatalf("/readyz while the app answers migrations_pending = %d, want 200", got)
	}
	sync("down")
	if got := readyz(); got != http.StatusServiceUnavailable {
		t.Fatalf("/readyz after migrations_pending, then a failure = %d, want 503 again", got)
	}
	sync("list")
	if got := readyz(); got != http.StatusOK {
		t.Fatalf("/readyz once the key list loaded = %d, want 200", got)
	}
	sync("down")
	if got := readyz(); got != http.StatusOK {
		t.Fatalf("/readyz after a loaded list, then a failure = %d, want 200 (a loaded list stays loaded)", got)
	}

	srv.ready.Store(false) // Shutdown has begun
	sync("pending")
	if got := readyz(); got != http.StatusServiceUnavailable {
		t.Errorf("/readyz while shutting down = %d, want 503", got)
	}
}
