package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// /readyz answers 503 while the gateway waits for its first key sync from
// the control plane, when one is set to run (on start or on an interval),
// and 200 once a key list has loaded. A gateway whose control plane runs no
// key sync, or that has none, is ready at once. Liveness never waits.
func TestReadyz_WaitsForTheFirstKeySyncOfAControlPlaneThatSyncsKeys(t *testing.T) {
	for _, tc := range []struct {
		name  string
		cp    config.ControlPlaneConfig
		waits bool
	}{
		{"no control plane", config.ControlPlaneConfig{}, false},
		{"sync on start", config.ControlPlaneConfig{URL: "http://app.invalid", SyncOnStartup: true}, true},
		{"periodic sync only", config.ControlPlaneConfig{URL: "http://app.invalid", SyncInterval: time.Minute}, true},
		{"both", config.ControlPlaneConfig{URL: "http://app.invalid", SyncOnStartup: true, SyncInterval: time.Minute}, true},
		{"no key sync (request logs only)", config.ControlPlaneConfig{URL: "http://app.invalid"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.ControlPlane = tc.cp
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			ks := auth.NewKeyStore(cfg.Auth)
			srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true) // Start has run

			status := func(path string) int {
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
				return w.Code
			}
			want := http.StatusOK
			if tc.waits {
				want = http.StatusServiceUnavailable
			}
			if got := status("/readyz"); got != want {
				t.Fatalf("/readyz before any key sync = %d, want %d", got, want)
			}
			for _, path := range []string{"/livez", "/healthz"} {
				if got := status(path); got != http.StatusOK {
					t.Errorf("%s before any key sync = %d, want 200", path, got)
				}
			}

			ks.SyncFromHashes([]auth.SyncedKey{{ID: "key-1", KeyHash: auth.HashKey("sk-synced-1")}})
			if got := status("/readyz"); got != http.StatusOK {
				t.Fatalf("/readyz once a key list loaded = %d, want 200", got)
			}

			srv.ready.Store(false) // Shutdown has begun
			if got := status("/readyz"); got != http.StatusServiceUnavailable {
				t.Errorf("/readyz while shutting down = %d, want 503", got)
			}
		})
	}
}

// A replica that the app pushed a key to one by one (POST /-/keys/sync)
// before its first key list turns ready when that list comes back empty,
// as a replica with no key does: the app lists no key, and the pushed key
// is kept.
func TestReadyz_TurnsReadyOnAnEmptyFirstListAfterAPush(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.ControlPlane = config.ControlPlaneConfig{URL: "http://app.invalid", SyncOnStartup: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ks := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	readyz := func() int {
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
		return w.Code
	}

	ks.LoadFromHashes([]auth.SyncedKey{{ID: "key-pushed", KeyHash: auth.HashKey("sk-pushed-before-the-list")}})
	if got := readyz(); got != http.StatusServiceUnavailable {
		t.Fatalf("/readyz after a push, before any key list = %d, want 503", got)
	}
	ks.SyncFromHashes(nil)
	if got := readyz(); got != http.StatusOK {
		t.Errorf("/readyz once an empty key list loaded = %d, want 200", got)
	}
	if ks.Authenticate("sk-pushed-before-the-list") == nil {
		t.Error("the empty list dropped the pushed key")
	}
}
