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
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The operator requires an already rolled-out compatible runtime, but never
// authorizes worker/write resumption. Gateway readiness is not the SQL receipt.
func TestHostedReadyDuringCompatibleBackfill(t *testing.T) {
	var schemaProbes atomic.Int32
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/readyz/" {
			schemaProbes.Add(1)
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
			return
		}
		fmt.Fprint(w, `{"status":true,"result":{}}`)
	}))
	defer cp.Close()
	keys := auth.NewKeyStore(config.AuthConfig{})
	keys.SyncFromHashes(nil)
	orgs := tenant.NewStore()
	if err := tenant.SyncFromControlPlane(context.Background(), cp.URL, "", orgs); err != nil {
		t.Fatal(err)
	}
	s := &Server{hosted: true, waitForKeyList: true, keyStore: keys, TenantStore: orgs, hostedRedisReady: func(context.Context) error { return nil }}
	s.ready.Store(true)
	w := httptest.NewRecorder()
	s.readyHandler(w, httptest.NewRequest("GET", "/readyz", nil))
	if w.Code != http.StatusOK || schemaProbes.Load() != 0 {
		t.Fatalf("compatible runtime unexpectedly gated on backend schema: %d, probes=%d", w.Code, schemaProbes.Load())
	}
	response, err := http.Get(cp.URL + "/readyz/")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusServiceUnavailable || schemaProbes.Load() != 1 {
		t.Fatal("schema-pending loopback control was not reached")
	}
}
