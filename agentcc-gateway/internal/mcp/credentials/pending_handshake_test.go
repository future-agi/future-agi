package credentials

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCredentialLifecyclePendingHandshakeStopsAtCredentialEviction(t *testing.T) {
	rig := newLifecycleRig(t)
	fixtures, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "lifetime"}, fixtures)
	if err != nil {
		t.Fatal(err)
	}
	started, release := make(chan struct{}), make(chan struct{})
	var evicted atomic.Bool
	var after atomic.Int32
	handler := mock.Handler()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Method == http.MethodPost {
			raw, readErr := io.ReadAll(req.Body)
			if readErr != nil {
				t.Error("synthetic request unreadable")
				return
			}
			req.Body = io.NopCloser(bytes.NewReader(raw))
			var message struct {
				Method string `json:"method"`
			}
			_ = json.Unmarshal(raw, &message)
			if message.Method == "initialize" {
				close(started)
				select {
				case <-release:
				case <-req.Context().Done():
					return
				}
			} else if evicted.Load() {
				after.Add(1)
			}
		}
		handler.ServeHTTP(w, req)
	}))
	defer server.Close()
	mock.SetPublicURL(server.URL)
	var cfg tenant.OrgConfig
	raw, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": map[string]any{
		"enabled": true, "servers": map[string]any{"issues": map[string]any{
			"url": server.URL + "/mcp", "era": "2025-06-18", "credential": map[string]any{"mode": "per_user"},
		}},
	}})
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	store.Set("org-a", &cfg)
	factory := upstream.NewFactory(upstream.Options{})
	manager := tenancy.New(tenancy.Options{Store: store, Deps: func() *mcp.Deps {
		return &mcp.Deps{ClientFactory: factory.Build, ManagedServers: noManagedServers{}}
	}})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = manager.Close(ctx)
	}()
	auth, _, denial := rig.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
	if denial != nil {
		t.Fatal("synthetic grant resolution failed")
	}
	_, pool, err := manager.Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := pool.Client("issues", "u1", *auth); done <- err }()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("synthetic initialization did not start")
	}
	if _, err := rig.resolver.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil {
		close(release)
		t.Fatal(err)
	}
	evicted.Store(true)
	select {
	case err := <-done:
		close(release)
		if err == nil {
			t.Fatal("evicted construction was published")
		}
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("evicted construction stayed active until the upstream answered")
	}
	if n := after.Load(); n != 0 {
		t.Fatalf("evicted in-progress client sent %d further upstream requests after eviction completed", n)
	}
}
