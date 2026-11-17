package credentials

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCredentialLifecycleSharedReplicaPendingConstruction(t *testing.T) {
	r, purger, source := stoppingResolvers(t)
	k := SharedKey("org-a", "issues")
	stoppingSeed(source, k, 1)
	h := independentR3Mock(t)
	defer h.server.Close()
	store := tenant.NewStore()
	data, _ := json.Marshal(map[string]any{"mcp": map[string]any{"enabled": true, "servers": map[string]any{"issues": map[string]any{"url": h.server.URL + "/mcp", "era": "2025-06-18", "credential": signedIn("synthetic")}}}})
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		t.Fatal(err)
	}
	store.Set("org-a", &cfg)
	built := make(chan struct{}, 1)
	factory := upstream.NewFactory(upstream.Options{})
	manager := tenancy.New(tenancy.Options{Store: store, InitialWait: -1, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
		return &mcp.Deps{SharedCredentials: r, ManagedServers: noManagedServers{}, ClientFactory: func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
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
		t.Fatal("shared handshake never started")
	}
	source.set(k, Token{}, &Refusal{Reason: RefusalRevoked})
	start := time.Now()
	if _, err := purger.PurgeCredentials(context.Background(), k.OrgID, "", k.ServerID); err != nil {
		close(h.release)
		t.Fatal(err)
	}
	h.evicted.Store(true)
	select {
	case <-built:
		t.Logf("shared construction cancellation=%s", time.Since(start))
	case <-time.After(2 * time.Second):
		close(h.release)
		t.Fatal("remote shared initialization exceeded cancellation bound")
	}
	close(h.release)
	if time.Since(start) > 2*time.Second {
		t.Fatal("remote shared initialization exceeded two seconds")
	}
	if h.after.Load() != 0 {
		t.Fatal("cancelled construction sent discovery requests")
	}
}

type stoppingManaged struct{ url string }

func (s stoppingManaged) ForOrg(string) []mcp.ClientConfig {
	return []mcp.ClientConfig{{ServerID: "fagi.search", URL: s.url, Era: "2025-06-18"}}
}

func TestCredentialLifecycleSuppliedPendingConstruction(t *testing.T) {
	h := independentR3Mock(t)
	defer h.server.Close()
	store := tenant.NewStore()
	configure := func(state string) {
		data, _ := json.Marshal(map[string]any{"mcp": map[string]any{"enabled": true, "managed_tools": map[string]any{"enabled": true, "credentials": map[string]any{"fagi.search": map[string]any{"mode": "shared", "state": state, "credential_id": "synthetic", "revision": 1, "secret": map[string]any{"type": "bearer", "value": "synthetic-customer"}}}}}})
		var cfg tenant.OrgConfig
		if err := json.Unmarshal(data, &cfg); err != nil {
			t.Fatal(err)
		}
		store.Set("org-a", &cfg)
	}
	configure("active")
	factory := upstream.NewFactory(upstream.Options{})
	built := make(chan struct{}, 1)
	manager := tenancy.New(tenancy.Options{Store: store, InitialWait: -1, Deps: func() *mcp.Deps {
		return &mcp.Deps{ManagedServers: stoppingManaged{h.server.URL + "/mcp"}, ClientFactory: func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
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
		t.Fatal("supplied handshake never started")
	}
	configure("revoked")
	h.evicted.Store(true)
	select {
	case <-built:
	case <-time.After(time.Second):
		close(h.release)
		t.Fatal("supplied initialization was not cancelled")
	}
	close(h.release)
	if h.after.Load() != 0 {
		t.Fatal("supplied initialization sent post-revocation discovery")
	}
}
