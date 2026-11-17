package credentials

import (
	"context"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/redis/go-redis/v9"
)

// Ported from the round-5 tenancy verifier (cold_admission_test.go). The
// remote revoke is the pushed revoked config plus the eviction naming the
// grant's revision, which is what Django's shared revoke now sends. The hook
// pauses the first admission script, the only Redis command admission issues.
type independentR5BindHook struct {
	entered, release chan struct{}
	once             atomic.Bool
}

func (h *independentR5BindHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *independentR5BindHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *independentR5BindHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		args := cmd.Args()
		if (cmd.Name() == "evalsha" || cmd.Name() == "eval") && len(args) > 4 {
			last, _ := args[4].(string)
			if strings.Contains(last, "#pushed-admitted:") && h.once.CompareAndSwap(false, true) {
				close(h.entered)
				<-h.release
			}
		}
		return next(ctx, cmd)
	}
}

// A cold replica still has the active config which preceded the remote revoke.
// No gateway has previously admitted this pushed identity, so there is no hash
// tombstone. Purging must not let that stale snapshot bind itself to the NEW
// epoch and start authenticated operations after eviction returned.
func TestIndependentR5ColdPushedSnapshotCannotAdoptPostPurgeEpoch(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		name, id := "org-server", "issues"
		if supplied {
			name, id = "supplied-server", "fagi.search"
		}
		t.Run(name, func(t *testing.T) {
			r, purger, _ := stoppingResolvers(t)
			defer r.purgeUserLifetimes("org-a", "", id)
			storeA, storeB := tenant.NewStore(), tenant.NewStore()
			storeA.Set("org-a", independentLiteralConfig(t, supplied, "active"))
			storeB.Set("org-a", independentLiteralConfig(t, supplied, "active"))
			storeB.Set("org-a", independentLiteralConfig(t, supplied, "revoked"))
			if err := purger.RevokePushedGrants(context.Background(), "org-a", id, independentLiteralRevision); err != nil {
				t.Fatal(err)
			}
			var calls atomic.Int32
			manager := tenancy.New(tenancy.Options{Store: storeA, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
				return &mcp.Deps{SharedCredentials: r, ManagedServers: independentLiteralManaged{enabled: supplied}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
					calls.Add(1)
					return &lifecycleClient{}, nil
				}}
			}})
			defer independentR3Close(manager)
			_, pool, err := manager.Resolve("org-a")
			if err != nil {
				t.Fatal(err)
			}
			if n := calls.Load(); n != 0 {
				t.Errorf("cold stale snapshot invoked credentialed factory %d times after remote revoke/purge returned", n)
			}
			client, err := pool.Client(id, "", mcp.AuthOverride{})
			if err == nil {
				if _, err := client.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); err == nil {
					t.Error("cold stale snapshot returned successful tool result with evicted pushed grant")
				}
				if _, err := client.ReadResource(context.Background(), "synthetic://item", mcp.CallOptions{}); err == nil {
					t.Error("cold stale snapshot returned successful resource result with evicted pushed grant")
				}
				if _, err := client.GetPrompt(context.Background(), "synthetic", nil, mcp.CallOptions{}); err == nil {
					t.Error("cold stale snapshot returned successful prompt result with evicted pushed grant")
				}
			}
		})
	}
}

// Pause the first distributed admission, before Redis has recorded its identity.
// A remote eviction completes while construction is pending. Admission must not
// reinterpret that old config as a newly issued credential when it resumes.
func TestIndependentR5PendingFirstPushedAdmissionCannotCrossEviction(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		name, id := "org-server", "issues"
		if supplied {
			name, id = "supplied-server", "fagi.search"
		}
		t.Run(name, func(t *testing.T) {
			r, purger, _ := stoppingResolvers(t)
			defer r.purgeUserLifetimes("org-a", "", id)
			hook := &independentR5BindHook{entered: make(chan struct{}), release: make(chan struct{})}
			r.cache.client.Redis().AddHook(hook)
			var once sync.Once
			release := func() { once.Do(func() { close(hook.release) }) }
			defer release()
			storeA, storeB := tenant.NewStore(), tenant.NewStore()
			storeA.Set("org-a", independentLiteralConfig(t, supplied, "active"))
			storeB.Set("org-a", independentLiteralConfig(t, supplied, "active"))
			var calls atomic.Int32
			manager := tenancy.New(tenancy.Options{Store: storeA, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
				return &mcp.Deps{SharedCredentials: r, ManagedServers: independentLiteralManaged{enabled: supplied}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { calls.Add(1); return &lifecycleClient{}, nil }}
			}})
			defer independentR3Close(manager)
			type result struct {
				pool mcp.TenantPool
				err  error
			}
			done := make(chan result, 1)
			go func() { _, p, err := manager.Resolve("org-a"); done <- result{p, err} }()
			select {
			case <-hook.entered:
			case <-time.After(time.Second):
				t.Fatal("first pushed admission did not reach Redis stand-in")
			}
			storeB.Set("org-a", independentLiteralConfig(t, supplied, "revoked"))
			if err := purger.RevokePushedGrants(context.Background(), "org-a", id, independentLiteralRevision); err != nil {
				t.Fatal(err)
			}
			release()
			var got result
			select {
			case got = <-done:
			case <-time.After(5 * time.Second):
				t.Fatal("released first admission did not finish")
			}
			if got.err != nil {
				t.Fatal(got.err)
			}
			if n := calls.Load(); n != 0 {
				t.Errorf("pending first admission invoked credentialed factory %d times after remote eviction returned", n)
			}
			client, err := got.pool.Client(id, "", mcp.AuthOverride{})
			if err == nil && lifecycleCall(client) == nil {
				t.Error("pending first admission published callable evicted pushed credential after remote purge returned")
			}
		})
	}
}
