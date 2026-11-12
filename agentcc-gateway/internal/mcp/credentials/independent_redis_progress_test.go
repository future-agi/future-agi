package credentials

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

type independentR4ProgressHook struct {
	entered, release chan struct{}
	blocked          atomic.Bool
}

func (h *independentR4ProgressHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h *independentR4ProgressHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *independentR4ProgressHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		args := cmd.Args()
		if cmd.Name() == "evalsha" && len(args) > 1 {
			if args[1] == personalPublish.Hash() && h.blocked.CompareAndSwap(false, true) {
				close(h.entered)
				<-h.release
			}
			if args[1] == purgePersonal.Hash() {
				return errors.New("synthetic Redis purge unavailable")
			}
		}
		return next(ctx, cmd)
	}
}
func TestIndependentR4SlowRedisDoesNotBlockUnrelatedIdentity(t *testing.T) {
	r, _ := independentR4Resolvers(t)
	source := r.tokens.(*lifecycleSource)
	source.set(keyA1, Token{AccessToken: "synthetic-blocked", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil)
	other := keyA1
	other.OrgID = "org-b"
	source.set(other, Token{AccessToken: "synthetic-other", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil)
	hook := &independentR4ProgressHook{entered: make(chan struct{}), release: make(chan struct{})}
	r.cache.client.Redis().AddHook(hook)
	publication := make(chan struct{})
	go func() { _, _ = r.userAuth(context.Background(), keyA1); close(publication) }()
	select {
	case <-hook.entered:
	case <-time.After(time.Second):
		close(hook.release)
		t.Fatal("publication never reached Redis stand-in")
	}
	purge := make(chan struct{})
	go func() {
		_, _ = r.PurgeCredentials(context.Background(), keyA1.OrgID, keyA1.UserID, keyA1.ServerID)
		close(purge)
	}()
	// Order by the purge outcome, not by observing an implementation lock.
	// Failed eviction must withdraw local grants without waiting for a blocked
	// publication, and must leave the unrelated organization able to resolve.
	select {
	case <-purge:
	case <-time.After(time.Second):
		t.Error("failed purge waited for a stalled Redis publication")
	}
	unrelated := make(chan error, 1)
	go func() { _, err := r.userAuth(context.Background(), other); unrelated <- err }()
	blocked := false
	select {
	case err := <-unrelated:
		if err != nil {
			t.Error(err)
		}
	case <-time.After(time.Second):
		blocked = true
	}
	close(hook.release)
	<-publication
	<-purge
	if blocked {
		<-unrelated
		t.Error("slow Redis publication plus a failed same-identity purge held the global lifetime registry lock and blocked an unrelated organization's credential resolution")
	}
}
