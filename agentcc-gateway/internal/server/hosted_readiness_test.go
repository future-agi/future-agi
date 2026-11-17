package server

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestHostedReadyRequiresCompleteOrgAndKeySyncAndRedis(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{})
	store := tenant.NewStore()
	s := &Server{hosted: true, waitForKeyList: true, keyStore: ks, TenantStore: store, hostedRedisReady: func(context.Context) error { return nil }}
	probe := func(want int) {
		t.Helper()
		w := httptest.NewRecorder()
		s.readyHandler(w, httptest.NewRequest("GET", "/readyz", nil))
		if w.Code != want {
			t.Fatalf("ready status = %d, want %d", w.Code, want)
		}
		live := httptest.NewRecorder()
		s.healthHandler(live, httptest.NewRequest("GET", "/livez", nil))
		if live.Code != 200 {
			t.Fatal("liveness must be process-only")
		}
	}
	probe(503)
	s.ready.Store(true)
	probe(503)
	answer := `{"status":false,"code":"migrations_pending"}`
	status := 503
	cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status); fmt.Fprint(w, answer) }))
	defer cp.Close()
	_ = auth.SyncKeysFromControlPlane(context.Background(), cp.URL, "", ks)
	probe(503) // bundled exception must not apply in hosted mode
	ks.SyncFromHashes(nil)
	probe(503) // keys alone are insufficient
	for _, tc := range []struct {
		status int
		body   string
	}{
		{401, `{"status":false}`},
		{200, `{"status":true,"result":{"org":false}}`},
		{200, `{"status":true,"result":{},"unbuilt_org_ids":["org"]}`},
		{200, `{"status":true}`},
	} {
		status, answer = tc.status, tc.body
		_ = tenant.SyncFromControlPlane(context.Background(), cp.URL, "", store)
		probe(503)
	}
	status, answer = 200, `{"status":true,"result":{}}`
	if err := tenant.SyncFromControlPlane(context.Background(), cp.URL, "", store); err != nil {
		t.Fatal(err)
	}
	probe(200)
	s.hostedRedisReady = func(context.Context) error { return errors.New("dependency unavailable") }
	probe(503)
	s.hostedRedisReady = func(context.Context) error { return nil }
	probe(200)
	s.ready.Store(false)
	probe(503)
}

func TestHostedReloadRequiresRestartForInfrastructure(t *testing.T) {
	before := config.DefaultConfig()
	after := config.DefaultConfig()
	if !hostedReloadCompatible(before, after) {
		t.Fatal("unchanged config refused")
	}
	after.ControlPlane.URL = "http://other-backend:8000"
	if hostedReloadCompatible(before, after) {
		t.Fatal("changed sync URL accepted without restarting sync")
	}
}

func TestHostedReadinessFalseAfterFailedListenerStart(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	registry, err := providers.NewRegistry(config.DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	s := &Server{httpServer: &http.Server{Addr: listener.Addr().String()}, registry: registry, engine: pipeline.NewEngine()}
	if err := s.Start(); err == nil {
		t.Fatal("expected occupied listener to fail")
	}
	w := httptest.NewRecorder()
	s.readyHandler(w, httptest.NewRequest("GET", "/readyz", nil))
	if w.Code != 503 {
		t.Fatalf("failed start remained ready: %d", w.Code)
	}
}
