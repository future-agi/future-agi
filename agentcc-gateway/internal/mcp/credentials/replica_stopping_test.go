package credentials

import (
	"context"
	"encoding/json"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type stoppingClient struct {
	lifecycleClient
	entered, release, cancelled chan struct{}
}

func (c *stoppingClient) answer(ctx context.Context) (json.RawMessage, error) {
	stop := context.AfterFunc(ctx, func() { close(c.cancelled) })
	defer stop()
	close(c.entered)
	<-c.release // Intentionally hostile: return success even if cancellation won.
	return json.RawMessage(`{"content":[]}`), nil
}
func (c *stoppingClient) CallTool(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}
func (c *stoppingClient) ReadResource(ctx context.Context, _ string, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}
func (c *stoppingClient) GetPrompt(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	return c.answer(ctx)
}

// Separate caches model separate processes: no shared in-memory invalidation
// registry can accidentally make the remote cancellation assertions pass.
func stoppingResolvers(t *testing.T) (*Resolver, *Resolver, *lifecycleSource) {
	t.Helper()
	db, prefix := testRedis(t)
	source := &lifecycleSource{tokens: map[Key]Token{}, refusals: map[Key]error{}}
	makeResolver := func() *Resolver {
		r, err := NewResolver(ResolverOptions{Tokens: source, Cache: NewTokenCache(db, testCodec(t), CacheOptions{Prefix: prefix}), Codec: testCodec(t)})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { r.purgeUserLifetimes("org-a", "", "issues"); r.purgeUserLifetimes("org-b", "", "issues") })
		return r
	}
	return makeResolver(), makeResolver(), source
}
func stoppingGrant(t *testing.T, r *Resolver, k Key) *mcp.AuthOverride {
	t.Helper()
	var a *mcp.AuthOverride
	var err error
	if k.Shared {
		a, err = (sharedSource{r: r, k: k}).Credential(context.Background())
	} else {
		a, err = r.userAuth(context.Background(), k)
	}
	if err != nil {
		t.Fatal("synthetic grant was refused", err)
	}
	return a
}
func stoppingSeed(source *lifecycleSource, k Key, version int) {
	source.set(k, Token{AccessToken: "synthetic-stopping-grant", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: version}, nil)
}
func stoppingAwait(t *testing.T, done <-chan struct{}, started time.Time, bound time.Duration) {
	t.Helper()
	timer := time.NewTimer(bound - time.Since(started))
	defer timer.Stop()
	select {
	case <-done:
	case <-timer.C:
		t.Fatalf("grant cancellation exceeded %s", bound)
	}
	elapsed := time.Since(started)
	if elapsed > bound {
		t.Fatalf("grant cancellation took %s, limit %s", elapsed, bound)
	}
	t.Logf("measured cancellation=%s bound=%s", elapsed, bound)
}

func TestCredentialLifecycleReplicaCancellationBounds(t *testing.T) {
	for _, shared := range []bool{false, true} {
		for _, lost := range []bool{false, true} {
			name := "personal"
			if shared {
				name = "shared"
			}
			if lost {
				name += "/lost-message"
			} else {
				name += "/pubsub"
			}
			t.Run(name, func(t *testing.T) {
				r, purger, source := stoppingResolvers(t)
				k := keyA1
				if shared {
					k = SharedKey("org-a", "issues")
				}
				stoppingSeed(source, k, 1)
				old := stoppingGrant(t, r, k)
				otherKey := k
				otherKey.OrgID = "org-b"
				stoppingSeed(source, otherKey, 1)
				other := stoppingGrant(t, r, otherKey)
				start := time.Now()
				bound := 2 * time.Second
				if lost {
					// Commit the same epoch transition without publishing a notification.
					// This models a dropped message, including a subscriber reconnect gap.
					epochKey := r.cache.personalEpochKey(k)
					if shared {
						epochKey = r.cache.sharedEpochKey(k)
					}
					if err := r.cache.client.Redis().Eval(context.Background(), "redis.call('INCR', KEYS[1]); return redis.call('DEL', KEYS[2])", []string{epochKey, r.cache.RedisKey(k)}).Err(); err != nil {
						t.Fatal(err)
					}
					bound = 5 * time.Second
				} else {
					if _, err := purger.PurgeCredentials(context.Background(), k.OrgID, k.UserID, k.ServerID); err != nil {
						t.Fatal(err)
					}
				}
				stoppingAwait(t, old.Lifetime.Done(), start, bound)
				stoppingSeed(source, k, 2)
				fresh := stoppingGrant(t, r, k)
				// Replay a delayed wakeup after reconnect. Comparisons name epochs, so
				// this cannot cancel the replacement or an unrelated organization.
				r.cache.checkLifetimes()
				if fresh.Check(context.Background()) != nil || other.Check(context.Background()) != nil {
					t.Fatal("eviction cancelled a newer grant or unrelated organization")
				}
			})
		}
	}
}

func TestCredentialLifecycleReplicaCompletionEveryMethod(t *testing.T) {
	testReplicaOperations(t, false)
}
func TestCredentialLifecycleReplicaActiveCancellationEveryMethod(t *testing.T) {
	testReplicaOperations(t, true)
}

func testReplicaOperations(t *testing.T, awaitCancellation bool) {
	t.Helper()
	for _, shared := range []bool{false, true} {
		mode := "per_user"
		if shared {
			mode = "shared"
		}
		for _, method := range []string{"tool", "resource", "prompt"} {
			t.Run(mode+"/"+method, func(t *testing.T) {
				r, purger, source := stoppingResolvers(t)
				k := keyA1
				if shared {
					k = SharedKey("org-a", "issues")
				}
				stoppingSeed(source, k, 1)
				raw := &stoppingClient{entered: make(chan struct{}), release: make(chan struct{}), cancelled: make(chan struct{})}
				defer func() {
					select {
					case <-raw.release:
					default:
						close(raw.release)
					}
				}()
				credential := map[string]any{"mode": mode}
				if shared {
					credential["kind"] = "oauth"
					credential["credential_id"] = "synthetic"
					credential["state"] = "active"
				}
				data, _ := json.Marshal(map[string]any{"mcp": map[string]any{"enabled": true, "servers": map[string]any{"issues": map[string]any{"url": "https://synthetic.example.test/mcp", "credential": credential}}}})
				var cfg tenant.OrgConfig
				if err := json.Unmarshal(data, &cfg); err != nil {
					t.Fatal(err)
				}
				store := tenant.NewStore()
				store.Set("org-a", &cfg)
				manager := tenancy.New(tenancy.Options{Store: store, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
					return &mcp.Deps{SharedCredentials: r, ManagedServers: noManagedServers{}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return raw, nil }}
				}})
				defer independentR3Close(manager)
				_, pool, err := manager.Resolve("org-a")
				if err != nil {
					t.Fatal(err)
				}
				auth := mcp.AuthOverride{}
				if !shared {
					auth = *stoppingGrant(t, r, k)
				}
				c, err := pool.Client("issues", k.UserID, auth)
				if err != nil {
					t.Fatal(err)
				}
				done := make(chan error, 1)
				go func() {
					var err error
					switch method {
					case "tool":
						_, err = c.CallTool(context.Background(), "search", nil, mcp.CallOptions{})
					case "resource":
						_, err = c.ReadResource(context.Background(), "synthetic://resource", mcp.CallOptions{})
					case "prompt":
						_, err = c.GetPrompt(context.Background(), "synthetic", nil, mcp.CallOptions{})
					}
					done <- err
				}()
				select {
				case <-raw.entered:
				case <-time.After(2 * time.Second):
					t.Fatal("synthetic operation did not start")
				}
				source.set(k, Token{}, &Refusal{Reason: RefusalRevoked})
				start := time.Now()
				if _, err := purger.PurgeCredentials(context.Background(), k.OrgID, k.UserID, k.ServerID); err != nil {
					close(raw.release)
					t.Fatal(err)
				}
				// The completion case releases immediately; the cancellation case
				// keeps the provider pending until its operation context is ended.
				if awaitCancellation {
					stoppingAwait(t, raw.cancelled, start, 2*time.Second)
				}
				close(raw.release)
				if err := <-done; err == nil {
					t.Fatal("revoked operation returned a successful result")
				}
				t.Logf("completion refused after eviction in %s", time.Since(start))
			})
		}
	}
}

// Expiry is authoritative even if the timer goroutine has not run yet.
func TestCredentialLifecycleSharedExpiryCompletionFence(t *testing.T) {
	r, _, source := stoppingResolvers(t)
	start := time.Now()
	var elapsed atomic.Int64
	r.now = func() time.Time { return start.Add(time.Duration(elapsed.Load())) }
	k := SharedKey("org-a", "issues")
	stoppingSeed(source, k, 1)
	auth := stoppingGrant(t, r, k)
	elapsed.Store(int64(2 * time.Hour))
	if auth.Check(context.Background()) == nil {
		t.Fatal("expired shared grant passed the completion fence")
	}
	select {
	case <-auth.Lifetime.Done():
	default:
		t.Fatal("expired shared grant retained a live lifetime")
	}
}
