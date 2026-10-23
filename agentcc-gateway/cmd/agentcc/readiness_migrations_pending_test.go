package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A new install with bundled datastores, as the Helm chart runs it: the
// bootstrap job that migrates the app's empty database is a post-install hook,
// and `helm install --wait` runs it only once the gateway reports ready.
// Until then the app's key list answers 503 migrations_pending (no key table,
// so no key made in the app) and its org configs fail. The gateway reports
// ready on that answer; once the app is migrated, its next startup sync loads
// the app's keys, and a key made in the app is accepted.
func TestReadiness_ReadyBeforeTheAppIsMigrated(t *testing.T) {
	provider := startChatProvider(t)
	var migrated atomic.Bool
	var keyListCalls atomic.Int32
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/agentcc/api-keys/bulk/" && !migrated.Load():
			keyListCalls.Add(1)
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"type":"api_error","code":"migrations_pending","detail":"The database has no gateway key table yet: its migrations have not run."}`)
		case r.URL.Path == "/agentcc/api-keys/bulk/":
			fmt.Fprintf(w, `{"status":true,"result":[{"id":"key-synced","name":"synced","key_hash":%q,"metadata":{"org_id":"org-r"}}]}`,
				auth.HashKey(syncedTestKey))
		case r.URL.Path == "/agentcc/org-configs/bulk/" && !migrated.Load():
			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprint(w, `{"status":false,"result":"Internal server error"}`)
		case r.URL.Path == "/agentcc/org-configs/bulk/":
			fmt.Fprint(w, `{"status":true,"result":{"org-r":{"providers":{"openai":{"enabled":true,"api_key":"org-key","models":["gpt-4o"]}}}}}`)
		default:
			fmt.Fprint(w, `{}`)
		}
	}))
	t.Cleanup(app.Close)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
providers:
  openai:
    base_url: `+provider.URL+`
    api_key: fake
    api_format: openai
    models: [gpt-4o]
auth:
  enabled: true
control_plane:
  url: `+app.URL+`
  sync_on_startup: true
admin:
  token: `+testAdminToken+`
`)

	for deadline := time.Now().Add(20 * time.Second); g.get("/readyz") != http.StatusOK; time.Sleep(20 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatalf("/readyz still answers 503 after 20 s while the app answers migrations_pending (key list asked for %d time(s)): `helm install --wait` would never run the bootstrap job", keyListCalls.Load())
		}
	}
	if keyListCalls.Load() == 0 {
		t.Fatal("the gateway reported ready before it asked the app for its keys")
	}

	migrated.Store(true) // the bootstrap job has run
	for deadline := time.Now().Add(45 * time.Second); ; time.Sleep(200 * time.Millisecond) {
		status, body := g.chatAs(syncedTestKey)
		if status == http.StatusOK {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("45 s after the app was migrated, its key still gets %d: %s", status, body)
		}
	}
	if code := g.get("/readyz"); code != http.StatusOK {
		t.Errorf("/readyz once the app's keys loaded = %d, want 200", code)
	}
}
