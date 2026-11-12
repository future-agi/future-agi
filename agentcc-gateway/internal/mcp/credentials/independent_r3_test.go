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

// Initialization is deliberately pending at a localhost provider. Successful
// revocation must stop its construction and prevent subsequent discovery I/O.
type independentR3Handshake struct {
	server           *httptest.Server
	started, release chan struct{}
	evicted          atomic.Bool
	after            atomic.Int32
}

func independentR3Mock(t *testing.T) *independentR3Handshake {
	t.Helper()
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
	h := &independentR3Handshake{started: make(chan struct{}), release: make(chan struct{})}
	handler := mock.Handler()
	h.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Method == http.MethodPost {
			raw, err := io.ReadAll(req.Body)
			if err != nil {
				t.Error("synthetic request unreadable")
				return
			}
			req.Body = io.NopCloser(bytes.NewReader(raw))
			var message struct {
				Method string `json:"method"`
			}
			_ = json.Unmarshal(raw, &message)
			if message.Method == "initialize" {
				close(h.started)
				select {
				case <-h.release:
				case <-req.Context().Done():
					return
				}
			} else if h.evicted.Load() {
				h.after.Add(1)
			}
		}
		handler.ServeHTTP(w, req)
	}))
	mock.SetPublicURL(h.server.URL)
	return h
}
func independentR3Config(t *testing.T, url, mode, state string, version int) *tenant.OrgConfig {
	t.Helper()
	credential := map[string]any{"mode": mode}
	if mode == "shared" {
		credential["credential_id"] = "synthetic-credential"
		credential["state"] = state
		credential["secret"] = map[string]any{"type": "bearer", "value": "synthetic-shared"}
		credential["revision"] = 1
	}
	raw, err := json.Marshal(map[string]any{"meta": map[string]any{"version": version}, "mcp": map[string]any{
		"enabled": true, "servers": map[string]any{"issues": map[string]any{"url": url + "/mcp", "era": "2025-06-18", "credential": credential}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}
func independentR3Close(m *tenancy.Manager) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = m.Close(ctx)
}

func TestIndependentR3SharedHandshakeStopsAfterRevocation(t *testing.T) {
	h := independentR3Mock(t)
	defer h.server.Close()
	store := tenant.NewStore()
	store.Set("org-a", independentR3Config(t, h.server.URL, "shared", "active", 1))
	factory := upstream.NewFactory(upstream.Options{})
	built := make(chan struct{}, 1)
	manager := tenancy.New(tenancy.Options{Store: store, InitialWait: -1, Deps: func() *mcp.Deps {
		return &mcp.Deps{ManagedServers: noManagedServers{}, ClientFactory: func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
			c, err := factory.Build(cfg)
			built <- struct{}{}
			return c, err
		}}
	}})
	defer independentR3Close(manager)
	if _, _, err := manager.Resolve("org-a"); err != nil {
		close(h.release)
		t.Fatal(err)
	}
	select {
	case <-h.started:
	case <-time.After(5 * time.Second):
		close(h.release)
		t.Fatal("initialization did not start")
	}
	store.Set("org-a", independentR3Config(t, h.server.URL, "shared", "revoked", 2))
	h.evicted.Store(true)
	close(h.release)
	select {
	case <-built:
	case <-time.After(5 * time.Second):
		t.Fatal("initialization did not finish")
	}
	if n := h.after.Load(); n != 0 {
		t.Fatalf("shared initialization sent %d further upstream requests after authoritative revocation completed", n)
	}
}

func TestIndependentR3RemoteEvictionCancelsPendingHandshake(t *testing.T) {
	for _, user := range []string{"u1", ""} {
		t.Run("purge-user="+user, func(t *testing.T) {
			rig := newLifecycleRig(t)
			purger, err := NewResolver(ResolverOptions{Tokens: rig.source, Cache: rig.resolver.cache, Codec: testCodec(t)})
			if err != nil {
				t.Fatal(err)
			}
			h := independentR3Mock(t)
			defer h.server.Close()
			store := tenant.NewStore()
			store.Set("org-a", independentR3Config(t, h.server.URL, "per_user", "active", 1))
			factory := upstream.NewFactory(upstream.Options{})
			manager := tenancy.New(tenancy.Options{Store: store, Deps: func() *mcp.Deps { return &mcp.Deps{ManagedServers: noManagedServers{}, ClientFactory: factory.Build} }})
			defer independentR3Close(manager)
			auth, _, denial := rig.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			if denial != nil {
				close(h.release)
				t.Fatal("synthetic grant refused")
			}
			_, pool, err := manager.Resolve("org-a")
			if err != nil {
				close(h.release)
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() { _, err := pool.Client("issues", "u1", *auth); done <- err }()
			select {
			case <-h.started:
			case <-time.After(5 * time.Second):
				close(h.release)
				t.Fatal("initialization did not start")
			}
			rig.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
			if _, err := purger.PurgeCredentials(context.Background(), "org-a", user, "issues"); err != nil {
				close(h.release)
				t.Fatal(err)
			}
			h.evicted.Store(true)
			stopped := false
			var result error
			select {
			case result = <-done:
				stopped = true
			case <-time.After(time.Second):
			}
			close(h.release)
			if !stopped {
				select {
				case result = <-done:
				case <-time.After(5 * time.Second):
					t.Fatal("construction did not finish after release")
				}
			}
			if result == nil {
				t.Error("remote eviction published a revoked client")
			}
			if n := h.after.Load(); n != 0 {
				t.Errorf("remote eviction allowed %d subsequent upstream requests", n)
			}
			if !stopped {
				t.Error("remote eviction left the pending handshake active until the provider answered")
			}
		})
	}
}

func TestIndependentR3RemoteEvictionCancelsActiveCall(t *testing.T) {
	rig := newLifecycleRig(t)
	old := rig.client(t, "org-a", "u1")
	other := rig.client(t, "org-a", "u2")
	otherOrg := rig.client(t, "org-b", "u1")
	purger, err := NewResolver(ResolverOptions{Tokens: rig.source, Cache: rig.resolver.cache, Codec: testCodec(t)})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		_, err := old.CallTool(ctx, "search", json.RawMessage(`{"wait":true}`), mcp.CallOptions{})
		done <- err
	}()
	select {
	case <-rig.started:
	case <-time.After(5 * time.Second):
		t.Fatal("active operation did not start")
	}
	rig.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
	if _, err := purger.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err == nil {
			t.Error("revoked active operation returned success")
		}
	case <-time.After(time.Second):
		cancel()
		<-done
		t.Error("remote eviction did not cancel the already-active operation")
	}
	if lifecycleCall(other) != nil || lifecycleCall(otherOrg) != nil {
		t.Error("remote eviction affected an unrelated member or organization")
	}
}
