package credentials

import (
	"context"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/redis/go-redis/v9"
)

type admissionOrderHook struct {
	entered, release chan struct{}
	pushed           bool
	epochKey         string
	blocked          atomic.Bool
}

func (h *admissionOrderHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h *admissionOrderHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *admissionOrderHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		args := cmd.Args()
		match := cmd.Name() == "get" && len(args) > 1 && args[1] == h.epochKey
		if h.pushed {
			match = cmd.Name() == "evalsha" && len(args) > 1 && args[1] == admitPushed.Hash()
		}
		if match && h.blocked.CompareAndSwap(false, true) {
			close(h.entered)
			<-h.release
		}
		return next(ctx, cmd)
	}
}

// Two callers resolving the SAME live grant may finish in either order. The
// stale-response fence protects a different successor, not arbitrary callers
// which share the current identity and epoch.
func TestSharedLifecycleConcurrentSameGrantAdmission(t *testing.T) {
	for _, pushed := range []bool{false, true} {
		name := "oauth"
		if pushed {
			name = "pushed"
		}
		t.Run(name, func(t *testing.T) {
			r, _, source := stoppingResolvers(t)
			k := SharedKey("org-a", "issues")
			// Match the sealed cache's millisecond precision so both responses
			// describe an identical deadline, not a legitimate shorter grant.
			source.set(k, Token{AccessToken: "synthetic-same-grant", ExpiresAt: time.Now().Add(time.Hour).Truncate(time.Millisecond), CredentialVersion: 1}, nil)
			hook := &admissionOrderHook{entered: make(chan struct{}), release: make(chan struct{}), pushed: pushed, epochKey: r.cache.sharedEpochKey(k)}
			r.cache.client.Redis().AddHook(hook)
			var once sync.Once
			release := func() { once.Do(func() { close(hook.release) }) }
			defer release()
			resolve := func() (*mcp.AuthOverride, error) {
				if pushed {
					return r.PushedCredential(context.Background(), k.OrgID, k.ServerID, mcp.PushedGrant{Identity: "synthetic-same-grant", Revision: 1})
				}
				return (sharedSource{r: r, k: k}).Credential(context.Background())
			}
			type result struct {
				auth *mcp.AuthOverride
				err  error
			}
			first := make(chan result, 1)
			go func() { a, err := resolve(); first <- result{a, err} }()
			select {
			case <-hook.entered:
			case <-time.After(time.Second):
				t.Fatal("first admission did not reach the Redis stand-in")
			}
			second, err := resolve()
			if err != nil {
				t.Fatal("concurrent admission of the current grant was refused", err)
			}
			release()
			var prior result
			select {
			case prior = <-first:
			case <-time.After(time.Second):
				t.Fatal("released same-grant admission did not complete")
			}
			if prior.err != nil || prior.auth == nil {
				t.Fatal("same-grant admission was refused solely because another caller finished first")
			}
			if prior.auth.Lifetime != second.Lifetime || prior.auth.Check(context.Background()) != nil || second.Check(context.Background()) != nil {
				t.Fatal("concurrent same-grant admissions did not retain their shared valid lifetime")
			}
		})
	}
}
