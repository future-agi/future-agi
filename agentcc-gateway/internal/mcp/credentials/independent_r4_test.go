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

// Independent per-process cache registries. Shared non-OAuth credentials are
// pushed to each replica's local store, but use the same all-server purge seam.
func independentR4Resolvers(t *testing.T) (*Resolver, *Resolver) {
	t.Helper()
	db, prefix := testRedis(t)
	source := &lifecycleSource{tokens: map[Key]Token{}, refusals: map[Key]error{}}
	makeResolver := func() *Resolver {
		r, err := NewResolver(ResolverOptions{Tokens: source, Cache: NewTokenCache(db, testCodec(t), CacheOptions{Prefix: prefix}), Codec: testCodec(t)})
		if err != nil {
			t.Fatal(err)
		}
		return r
	}
	return makeResolver(), makeResolver()
}

// The vault's grant revision of the synthetic entry credential (push-only).
const independentR4Revision = 1

func independentR4Config(t *testing.T, supplied bool, url, state string) *tenant.OrgConfig {
	t.Helper()
	cred := map[string]any{"mode": "shared", "state": state, "credential_id": "synthetic-entry", "revision": independentR4Revision, "secret": map[string]any{"type": "bearer", "value": "synthetic-entry-grant"}}
	settings := map[string]any{"enabled": true}
	if supplied {
		settings["managed_tools"] = map[string]any{"enabled": true, "credentials": map[string]any{"fagi.search": cred}}
	} else {
		settings["servers"] = map[string]any{"issues": map[string]any{"url": url, "era": "2025-06-18", "credential": cred}}
	}
	data, _ := json.Marshal(map[string]any{"mcp": settings})
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}

type independentR4Managed struct{ url string }

func (s independentR4Managed) ForOrg(string) []mcp.ClientConfig {
	return []mcp.ClientConfig{{ServerID: "fagi.search", URL: s.url, Era: "2025-06-18"}}
}

type independentR4Completion struct {
	lifecycleClient
	entered, release chan struct{}
}

func (c *independentR4Completion) CallTool(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	close(c.entered)
	<-c.release
	return json.RawMessage(`{"content":[]}`), nil
}
func TestIndependentR4PushedSharedReplicaCompletionFence(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		mode, id := "org-shared-secret", "issues"
		if supplied {
			mode, id = "supplied-server", "fagi.search"
		}
		t.Run(mode, func(t *testing.T) {
			r, purger := independentR4Resolvers(t)
			storeA, storeB := tenant.NewStore(), tenant.NewStore()
			url := "https://synthetic.example.test/mcp"
			storeA.Set("org-a", independentR4Config(t, supplied, url, "active"))
			storeB.Set("org-a", independentR4Config(t, supplied, url, "active"))
			raw := &independentR4Completion{entered: make(chan struct{}), release: make(chan struct{})}
			managed := noManagedServers{}
			depsA := &mcp.Deps{SharedCredentials: r, ManagedServers: managed, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return raw, nil }}
			depsB := &mcp.Deps{SharedCredentials: purger, ManagedServers: managed, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return &lifecycleClient{}, nil }}
			if supplied {
				depsA.ManagedServers = independentR4Managed{url}
				depsB.ManagedServers = independentR4Managed{url}
			}
			a := tenancy.New(tenancy.Options{Store: storeA, Deps: func() *mcp.Deps { return depsA }})
			b := tenancy.New(tenancy.Options{Store: storeB, Deps: func() *mcp.Deps { return depsB }})
			defer independentR3Close(a)
			defer independentR3Close(b)
			_, pool, err := a.Resolve("org-a")
			if err != nil {
				t.Fatal(err)
			}
			if _, _, err = b.Resolve("org-a"); err != nil {
				t.Fatal(err)
			}
			c, err := pool.Client(id, "", mcp.AuthOverride{})
			if err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() { _, err := c.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); done <- err }()
			select {
			case <-raw.entered:
			case <-time.After(time.Second):
				close(raw.release)
				t.Fatal("operation did not start")
			}
			storeB.Set("org-a", independentR4Config(t, supplied, url, "revoked"))
			_, err = purger.PurgeCredentials(context.Background(), "org-a", "", id)
			close(raw.release)
			result := <-done
			if err != nil {
				t.Fatal(err)
			}
			if result == nil {
				t.Fatal("replica A returned success with an evicted pushed shared grant after replica B revoked its entry and confirmed Redis purge")
			}
		})
	}
}
func TestIndependentR4PushedSharedReplicaConstructionCancellation(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		mode, id := "org-shared-secret", "issues"
		if supplied {
			mode, id = "supplied-server", "fagi.search"
		}
		t.Run(mode, func(t *testing.T) {
			r, purger := independentR4Resolvers(t)
			h := independentR3Mock(t)
			defer h.server.Close()
			storeA, storeB := tenant.NewStore(), tenant.NewStore()
			url := h.server.URL + "/mcp"
			storeA.Set("org-a", independentR4Config(t, supplied, url, "active"))
			storeB.Set("org-a", independentR4Config(t, supplied, url, "active"))
			factory := upstream.NewFactory(upstream.Options{})
			built := make(chan error, 1)
			deps := &mcp.Deps{SharedCredentials: r, ManagedServers: noManagedServers{}, ClientFactory: func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
				c, err := factory.Build(cfg)
				built <- err
				return c, err
			}}
			if supplied {
				deps.ManagedServers = independentR4Managed{url}
			}
			a := tenancy.New(tenancy.Options{Store: storeA, InitialWait: -1, RetryInitial: time.Hour, Deps: func() *mcp.Deps { return deps }})
			defer independentR3Close(a)
			if _, _, err := a.Resolve("org-a"); err != nil {
				close(h.release)
				t.Fatal(err)
			}
			select {
			case <-h.started:
			case <-time.After(5 * time.Second):
				close(h.release)
				t.Fatal("initialization did not start")
			}
			storeB.Set("org-a", independentR4Config(t, supplied, url, "revoked"))
			start := time.Now()
			if _, err := purger.PurgeCredentials(context.Background(), "org-a", "", id); err != nil {
				close(h.release)
				t.Fatal(err)
			}
			h.evicted.Store(true)
			stopped := false
			select {
			case <-built:
				stopped = true
			case <-time.After(5 * time.Second):
			}
			elapsed := time.Since(start)
			close(h.release)
			if !stopped {
				select {
				case <-built:
				case <-time.After(5 * time.Second):
					t.Fatal("factory never finished after provider release")
				}
			}
			t.Logf("remote pushed shared construction cancellation observed=%t elapsed=%s post-eviction-requests=%d", stopped, elapsed, h.after.Load())
			if !stopped {
				t.Error("replica A initialization still active after the five-second cancellation backstop bound")
			}
			if h.after.Load() != 0 {
				t.Error("replica A sent discovery requests with an evicted pushed shared grant")
			}
		})
	}
}
