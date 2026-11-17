package credentials

import (
	"context"
	"net"
	"sync/atomic"
	"testing"

	"github.com/redis/go-redis/v9"
)

type independentR4LockHook struct {
	u      *userLifetime
	held   atomic.Int32
	checks atomic.Int32
}

func (h *independentR4LockHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h *independentR4LockHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *independentR4LockHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "evalsha" || cmd.Name() == "eval" {
			h.checks.Add(1)
			if h.u.mu.TryLock() {
				h.u.mu.Unlock()
			} else {
				h.held.Add(1)
			}
		}
		return next(ctx, cmd)
	}
}
func TestIndependentR4NoCredentialEntryLockAcrossRedis(t *testing.T) {
	for _, shared := range []bool{false, true} {
		name := "personal"
		if shared {
			name = "shared-oauth"
		}
		t.Run(name, func(t *testing.T) {
			r, _, source := stoppingResolvers(t)
			k := keyA1
			if shared {
				k = SharedKey("org-a", "issues")
			}
			stoppingSeed(source, k, 1)
			hook := &independentR4LockHook{u: r.users.get(k)}
			r.cache.client.Redis().AddHook(hook)
			_ = stoppingGrant(t, r, k)
			if hook.checks.Load() == 0 {
				t.Fatal("distributed epoch publication was not exercised")
			}
			if hook.held.Load() != 0 {
				t.Fatalf("credential entry mutex held across %d Redis epoch operations", hook.held.Load())
			}
		})
	}
}
