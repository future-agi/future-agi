package sessionlease

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Requires an isolated Redis. This fixture never flushes a database or deletes
// tombstones; the executor destroys the dedicated Redis instance afterward.
func nativeStore(t *testing.T) (*Store, Request) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("NOT_QUALIFIED: isolated real Redis fixture required")
	}
	s, err := NewSingle(redis.Options{Addr: addr}, fixturePolicy())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	r := fixtureRequest(fmt.Sprintf("synthetic-%d", time.Now().UnixNano()))
	r.Scope.OrgID = r.ID
	return s, r
}
func TestNativeDuplicateRenewReleaseAndNoResurrection(t *testing.T) {
	s, r := nativeStore(t)
	ctx := context.Background()
	first, err := s.Acquire(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.Acquire(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if !first.AbsoluteDeadline.Equal(second.AbsoluteDeadline) || !first.ExpiresAt.Equal(second.ExpiresAt) {
		t.Fatal("duplicate extended grant")
	}
	renewed, err := s.Renew(ctx, r.Binding, false)
	if err != nil {
		t.Fatal(err)
	}
	if !renewed.IdleDeadline.Equal(first.IdleDeadline) || !renewed.AbsoluteDeadline.Equal(first.AbsoluteDeadline) {
		t.Fatal("renew extended non-activity ceilings")
	}
	wrong := r.Binding
	wrong.Scope.WorkspaceID = "synthetic-other"
	if _, err := s.Renew(ctx, wrong, true); err != ErrBinding {
		t.Fatalf("cross-workspace: %v", err)
	}
	if err := s.Release(ctx, r.Binding); err != nil {
		t.Fatal(err)
	}
	if err := s.Release(ctx, r.Binding); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Renew(ctx, r.Binding, true); err != ErrGone {
		t.Fatal("renew resurrected")
	}
	if _, err := s.Acquire(ctx, r); err != ErrGone {
		t.Fatal("acquire resurrected")
	}
}
func TestNativeSiblingQuotaAndReleaseBeforeAcquire(t *testing.T) {
	s, r := nativeStore(t)
	ctx := context.Background()
	if err := s.Release(ctx, r.Binding); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Acquire(ctx, r); err != ErrGone {
		t.Fatal("late acquire survived release")
	}
	r.ID += "-active"
	if _, err := s.Acquire(ctx, r); err != nil {
		t.Fatal(err)
	}
	blocked := r
	blocked.ID += "-blocked"
	if _, err := s.Acquire(ctx, blocked); err != ErrCapacity {
		t.Fatalf("session cap: %v", err)
	}
	sibling := blocked
	sibling.ID += "-sibling"
	sibling.Scope.WorkspaceID += "-sibling"
	if _, err := s.Acquire(ctx, sibling); err != nil {
		t.Fatalf("sibling starvation: %v", err)
	}
	_ = s.Release(ctx, r.Binding)
	_ = s.Release(ctx, sibling.Binding)
}
func TestNativeExpiryAndPoison(t *testing.T) {
	s, r := nativeStore(t)
	ctx := context.Background()
	r.TTL = 25 * time.Millisecond
	if _, err := s.Acquire(ctx, r); err != nil {
		t.Fatal(err)
	}
	time.Sleep(40 * time.Millisecond)
	if _, err := s.Renew(ctx, r.Binding, true); err != ErrGone {
		t.Fatal("expired lease revived")
	}
	fresh := r
	fresh.ID += "-new"
	if _, err := s.Acquire(ctx, fresh); err != nil {
		t.Fatalf("expired occupancy retained: %v", err)
	}
	route := routeFor(fresh, s.policy)
	if _, err := s.eval(ctx, "return redis.call('SET',KEYS[1],'synthetic-poison')", []string{route.keys[6]}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Renew(ctx, fresh.Binding, true); err != ErrCorrupt {
		t.Fatalf("poison ignored: %v", err)
	}
}

func TestNativePolicyMismatchUsesCleanGuard(t *testing.T) {
	s, r := nativeStore(t) // Independent org/purpose: never shares the poison case.
	ctx := context.Background()
	if _, err := s.Acquire(ctx, r); err != nil {
		t.Fatal(err)
	}
	if err := s.Release(ctx, r.Binding); err != nil {
		t.Fatal(err)
	}
	// Prove same-policy admission works, with capacity free for the drift case.
	healthy := r
	healthy.ID += "-healthy"
	if _, err := s.Acquire(ctx, healthy); err != nil {
		t.Fatalf("clean same-policy control: %v", err)
	}
	if err := s.Release(ctx, healthy.Binding); err != nil {
		t.Fatal(err)
	}
	route := routeFor(r, s.policy)
	guard, err := s.eval(ctx, "return redis.call('EXISTS',KEYS[1])", []string{route.keys[6]})
	if err != nil || guard != int64(0) {
		t.Fatalf("policy fixture is poisoned: %v %v", guard, err)
	}
	digest, err := s.eval(ctx, "return redis.call('HGET',KEYS[1],'digest')", []string{route.keys[5]})
	if err != nil || digest != policyDigest(s.policy) {
		t.Fatalf("old policy not established: %v %v", digest, err)
	}
	other := *s
	other.policy.Version = "synthetic-other-policy"
	drift := r
	drift.ID += "-policy"
	if _, err := other.Acquire(ctx, drift); err != ErrBinding {
		t.Fatalf("policy drift: got %v, want ErrBinding", err)
	}
}
