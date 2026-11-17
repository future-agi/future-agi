package credentials

import (
	"context"
	"encoding/json"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type independentLiteralSource struct{}

func (independentLiteralSource) Fetch(context.Context, Key) (Token, error) {
	return Token{}, errors.New("no OAuth grant expected")
}

type independentLiteralManaged struct{ enabled bool }

func (s independentLiteralManaged) ForOrg(string) []mcp.ClientConfig {
	if !s.enabled {
		return nil
	}
	return []mcp.ClientConfig{{ServerID: "fagi.search", URL: "https://synthetic.example.test/mcp"}}
}

type independentLiteralClient struct {
	closed  atomic.Bool
	entered chan context.Context
	release chan struct{}
}

func (c *independentLiteralClient) Healthy() bool { return !c.closed.Load() }
func (c *independentLiteralClient) Close() error  { c.closed.Store(true); return nil }
func (c *independentLiteralClient) ListTools(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"search"}`)}, nil
}
func (c *independentLiteralClient) ListResources(context.Context) ([]json.RawMessage, error) {
	return nil, nil
}
func (c *independentLiteralClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return nil, nil
}
func (c *independentLiteralClient) answer(ctx context.Context) (json.RawMessage, error) {
	c.entered <- ctx
	<-c.release
	return json.RawMessage(`{"content":[]}`), nil
}
func (c *independentLiteralClient) CallTool(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}
func (c *independentLiteralClient) ReadResource(ctx context.Context, _ string, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}
func (c *independentLiteralClient) GetPrompt(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}

// The vault's grant revision of the synthetic literal credential (push-only).
const independentLiteralRevision = 1

func independentLiteralConfig(t *testing.T, supplied bool, state string) *tenant.OrgConfig {
	t.Helper()
	credential := map[string]any{"mode": "shared", "credential_id": "synthetic-literal", "revision": independentLiteralRevision, "state": state, "secret": map[string]any{"type": "bearer", "value": "synthetic-literal-grant"}}
	cfg := map[string]any{"enabled": true}
	if supplied {
		cfg["managed_tools"] = map[string]any{"enabled": true, "credentials": map[string]any{"fagi.search": credential}}
	} else {
		cfg["servers"] = map[string]any{"issues": map[string]any{"url": "https://synthetic.example.test/mcp", "credential": credential}}
	}
	raw, err := json.Marshal(map[string]any{"mcp": cfg})
	if err != nil {
		t.Fatal(err)
	}
	var out tenant.OrgConfig
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return &out
}
func TestIndependentR4LiteralSharedReplicaFence(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		name, id := "org-server", "issues"
		if supplied {
			name, id = "supplied-server", "fagi.search"
		}
		for _, local := range []bool{true, false} {
			location := "remote"
			if local {
				location = "local-control"
			}
			for _, method := range []string{"tool", "resource", "prompt"} {
				t.Run(name+"/"+location+"/"+method, func(t *testing.T) {
					db, prefix := testRedis(t)
					purger, err := NewResolver(ResolverOptions{Tokens: independentLiteralSource{}, Cache: NewTokenCache(db, testCodec(t), CacheOptions{Prefix: prefix}), Codec: testCodec(t)})
					if err != nil {
						t.Fatal(err)
					}
					storeA, storeB := tenant.NewStore(), tenant.NewStore()
					if local {
						storeB = storeA
					}
					storeA.Set("org-a", independentLiteralConfig(t, supplied, "active"))
					storeB.Set("org-a", independentLiteralConfig(t, supplied, "active"))
					raw := &independentLiteralClient{entered: make(chan context.Context, 1), release: make(chan struct{})}
					manager := tenancy.New(tenancy.Options{Store: storeA, Deps: func() *mcp.Deps {
						return &mcp.Deps{SharedCredentials: purger, ManagedServers: independentLiteralManaged{enabled: supplied}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return raw, nil }}
					}})
					defer func() {
						ctx, cancel := context.WithTimeout(context.Background(), time.Second)
						defer cancel()
						_ = manager.Close(ctx)
					}()
					_, pool, err := manager.Resolve("org-a")
					if err != nil {
						t.Fatal(err)
					}
					client, err := pool.Client(id, "", mcp.AuthOverride{})
					if err != nil {
						t.Fatal(err)
					}
					done := make(chan error, 1)
					go func() {
						var err error
						switch method {
						case "tool":
							_, err = client.CallTool(context.Background(), "search", nil, mcp.CallOptions{})
						case "resource":
							_, err = client.ReadResource(context.Background(), "synthetic://item", mcp.CallOptions{})
						case "prompt":
							_, err = client.GetPrompt(context.Background(), "synthetic", nil, mcp.CallOptions{})
						}
						done <- err
					}()
					select {
					case <-raw.entered:
					case <-time.After(time.Second):
						t.Fatal("synthetic operation did not enter")
					}
					// The revoke push reaches replica B. The completion fence at A must not
					// depend on a later, periodic config fetch reaching its independent store.
					storeB.Set("org-a", independentLiteralConfig(t, supplied, "revoked"))
					if _, err := purger.PurgeCredentials(context.Background(), "org-a", "", id); err != nil {
						close(raw.release)
						t.Fatal(err)
					}
					close(raw.release)
					select {
					case err := <-done:
						if err == nil {
							t.Error("pushed shared grant returned success after another replica completed revoke and purge")
						}
					case <-time.After(time.Second):
						t.Fatal("released synthetic provider did not finish")
					}
				})
			}
		}
	}
}
func TestIndependentR4LiteralSharedReplicaCancellation(t *testing.T) {
	db, prefix := testRedis(t)
	purger, err := NewResolver(ResolverOptions{Tokens: independentLiteralSource{}, Cache: NewTokenCache(db, testCodec(t), CacheOptions{Prefix: prefix}), Codec: testCodec(t)})
	if err != nil {
		t.Fatal(err)
	}
	storeA, storeB := tenant.NewStore(), tenant.NewStore()
	storeA.Set("org-a", independentLiteralConfig(t, false, "active"))
	storeB.Set("org-a", independentLiteralConfig(t, false, "active"))
	raw := &independentLiteralClient{entered: make(chan context.Context, 1), release: make(chan struct{})}
	manager := tenancy.New(tenancy.Options{Store: storeA, Deps: func() *mcp.Deps {
		return &mcp.Deps{SharedCredentials: purger, ManagedServers: independentLiteralManaged{}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return raw, nil }}
	}})
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = manager.Close(ctx)
	}()
	_, pool, err := manager.Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	client, err := pool.Client("issues", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := client.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); done <- err }()
	var active context.Context
	select {
	case active = <-raw.entered:
	case <-time.After(time.Second):
		t.Fatal("synthetic operation did not enter")
	}
	storeB.Set("org-a", independentLiteralConfig(t, false, "revoked"))
	start := time.Now()
	if _, err := purger.PurgeCredentials(context.Background(), "org-a", "", "issues"); err != nil {
		close(raw.release)
		t.Fatal(err)
	}
	select {
	case <-active.Done():
		t.Logf("remote literal grant cancelled in %s", time.Since(start))
	case <-time.After(5 * time.Second):
		t.Error("remote pushed shared grant context remained live beyond five-second backstop")
	}
	close(raw.release)
	if <-done == nil {
		t.Error("remote literal grant completed successfully after cancellation bound")
	}
}
