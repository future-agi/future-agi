package credentials

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type replicaCompletionClient struct {
	lifecycleClient
	entered chan struct{}
	release chan struct{}
}

func (c *replicaCompletionClient) CallTool(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	close(c.entered)
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-c.release:
		return json.RawMessage(`{"content":[]}`), nil
	}
}

// A response belonging to an evicted grant is refused even when the caller
// retains the original handle and the provider finishes after the purge.
func TestIndependentReplicaCompletionAfterEvictionIsRefused(t *testing.T) {
	for _, local := range []bool{true, false} {
		name := "another-resolver"
		if local {
			name = "same-resolver"
		}
		t.Run(name, func(t *testing.T) {
			for _, user := range []string{"u1", ""} {
				t.Run("purge-user="+user, func(t *testing.T) {
					rig := newLifecycleRig(t)
					purger := rig.resolver
					if !local {
						var err error
						purger, err = NewResolver(ResolverOptions{Tokens: rig.source, Cache: rig.resolver.cache, Codec: testCodec(t)})
						if err != nil {
							t.Fatal(err)
						}
					}
					store := tenant.NewStore()
					var cfg tenant.OrgConfig
					if err := json.Unmarshal([]byte(`{"org_id":"org-a","version":1,"mcp":{"enabled":true,"servers":{"issues":{"url":"https://synthetic.example.test/mcp","credential":{"mode":"per_user"}}}}}`), &cfg); err != nil {
						t.Fatal(err)
					}
					store.Set("org-a", &cfg)
					upstream := &replicaCompletionClient{entered: make(chan struct{}), release: make(chan struct{})}
					manager := tenancy.New(tenancy.Options{Store: store, Deps: func() *mcp.Deps {
						return &mcp.Deps{ManagedServers: noManagedServers{}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return upstream, nil }}
					}})
					defer func() {
						ctx, cancel := context.WithTimeout(context.Background(), time.Second)
						defer cancel()
						_ = manager.Close(ctx)
					}()
					auth, _, denial := rig.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
					if denial != nil {
						t.Fatal("initial resolution failed")
					}
					_, pool, err := manager.Resolve("org-a")
					if err != nil {
						t.Fatal(err)
					}
					prior, err := pool.Client("issues", "u1", *auth)
					if err != nil {
						t.Fatal(err)
					}
					done := make(chan error, 1)
					go func() { _, err := prior.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); done <- err }()
					select {
					case <-upstream.entered:
					case <-time.After(time.Second):
						close(upstream.release)
						t.Fatal("synthetic operation did not start")
					}
					rig.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
					_, err = purger.PurgeCredentials(context.Background(), "org-a", user, "issues")
					close(upstream.release)
					result := <-done
					if err != nil {
						t.Fatal(err)
					}
					if result == nil {
						t.Error("operation completed successfully with a revoked grant after another resolver confirmed eviction")
					}
				})
			}
		})
	}
}
