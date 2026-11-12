package credentials

import (
	"context"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/redis/go-redis/v9"
)

// Ported from the round-5 lifecycle verifier (delayed_admission_test.go). The
// hook pauses the first admission's Redis command; the eviction names the old
// grant's revision, and the successor carries a newer one.
type verifierR5PushedProvider interface {
	PushedCredential(context.Context, string, string, mcp.PushedGrant) (*mcp.AuthOverride, error)
}
type verifierR5BindHook struct {
	key              string
	entered, release chan struct{}
	blocked          atomic.Bool
}

func (h *verifierR5BindHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h *verifierR5BindHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *verifierR5BindHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		args := cmd.Args()
		if cmd.Name() == "evalsha" && len(args) > 4 && args[4] == h.key && h.blocked.CompareAndSwap(false, true) {
			close(h.entered)
			<-h.release
		}
		return next(ctx, cmd)
	}
}

// The eviction occurs while the first admission of the old config is pending
// at Redis. A new grant then wins admission before the delayed old request.
// This must neither admit the old grant under the new epoch nor cancel its
// already-admitted successor. All ordering is by command handshakes.
func TestVerifierR5DelayedFirstAdmissionPreservesSuccessor(t *testing.T) {
	r, purger := independentR4Resolvers(t)
	defer r.purgeUserLifetimes("org-a", "", "issues")
	provider, ok := any(r).(verifierR5PushedProvider)
	if !ok {
		t.Fatal("pushed construction has no distributed grant admission")
	}
	k := SharedKey("org-a", "issues")
	hook := &verifierR5BindHook{key: r.cache.pushedAdmittedKey(k), entered: make(chan struct{}), release: make(chan struct{})}
	r.cache.client.Redis().AddHook(hook)
	oldGrant := mcp.PushedGrant{Identity: "synthetic-pending-old", Revision: 1}
	oldDone := make(chan error, 1)
	go func() {
		_, err := provider.PushedCredential(context.Background(), k.OrgID, k.ServerID, oldGrant)
		oldDone <- err
	}()
	select {
	case <-hook.entered:
	case <-time.After(time.Second):
		close(hook.release)
		t.Fatal("old admission never reached Redis handshake")
	}
	if err := purger.RevokePushedGrants(context.Background(), k.OrgID, k.ServerID, oldGrant.Revision); err != nil {
		close(hook.release)
		t.Fatal(err)
	}
	fresh, err := provider.PushedCredential(context.Background(), k.OrgID, k.ServerID, mcp.PushedGrant{Identity: "synthetic-successor", Revision: 2})
	if err != nil {
		close(hook.release)
		t.Fatal("successor was refused", err)
	}
	close(hook.release)
	select {
	case err := <-oldDone:
		if err == nil {
			t.Error("pre-eviction pending admission adopted the post-eviction epoch")
		}
	case <-time.After(time.Second):
		t.Fatal("released admission did not complete")
	}
	// Even if the local sequence fence rejects the first response, its Redis
	// side effect must not authorize the evicted identity for a later retry.
	if _, err := provider.PushedCredential(context.Background(), k.OrgID, k.ServerID, oldGrant); err == nil {
		t.Error("delayed pre-eviction identity bound itself to the new epoch and was admitted on retry")
	}
	if fresh.Check(context.Background()) != nil || fresh.Lifetime.Err() != nil {
		t.Error("late evicted admission retry cancelled the already-admitted successor")
	}
}
