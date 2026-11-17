package auth

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// (c): a real concurrent burst on a fresh resident entry must share one
// RevalidateCached operation, not just return equivalent authority N times.
func TestPrimaryWindowConcurrentBurstOneRead(t *testing.T) {
	const n = 32
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryWindowFixture(t, kind)
			held := newSurvivorBarrier()
			defer held.open()
			var first sync.Once
			f.r.recheck = func(ctx context.Context, _ *ResolveResult) (bool, error) {
				first.Do(func() { close(held.entered) })
				select {
				case <-held.release:
					return true, nil
				case <-ctx.Done():
					return false, ctx.Err()
				}
			}
			start := make(chan struct{})
			arrived := make(chan struct{}, n)
			done := make(chan survivorOutcome, n)
			for i := 0; i < n; i++ {
				go func() {
					<-start
					probe := &survivorContextProbe{Context: context.Background(), beforeErr: func(call int32) {
						if call == 1 {
							arrived <- struct{}{}
						}
					}}
					got, err := f.a.Authenticate(probe, f.api, f.secret)
					done <- survivorOutcome{got, err}
				}()
			}
			close(start)
			survivorWait(t, held.entered, "burst PRIMARY read")
			for i := 0; i < n; i++ {
				survivorWait(t, arrived, "burst caller entry")
			}
			held.open()
			for i := 0; i < n; i++ {
				got := survivorReceive(t, done)
				if got.result != f.prior || got.err != nil {
					t.Fatalf("burst caller lost exact authority: %v", got.err)
				}
			}
			f.counts(t, 1, 0)
			f.authenticate(t)
			f.counts(t, 1, 0)
		})
	}
}

// (d): a blocked credential cannot serialize an unrelated one. Using the same
// result pointer in both entries also detects accidentally pointer-global state.
func TestPrimaryWindowDistinctCredentialsIndependent(t *testing.T) {
	f := newPrimaryWindowFixture(t, "personal")
	const otherAPI = "independent-window-key"
	otherKey := CacheKey(otherAPI, f.secret)
	survivorPublish(t, f.a.cache, otherKey, f.prior)
	held := newSurvivorBarrier()
	defer held.open()
	var calls atomic.Int32
	f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
		if calls.Add(1) == 1 {
			held.stop()
		}
		return true, nil
	}
	first := survivorAuthenticate(f.a, context.Background(), f.api, f.secret)
	survivorWait(t, held.entered, "first credential PRIMARY read")
	for i := 0; i < 2; i++ {
		got := survivorReceive(t, survivorAuthenticate(f.a, context.Background(), otherAPI, f.secret))
		if got.result != f.prior || got.err != nil {
			t.Fatalf("unrelated credential blocked or refused: %v", got.err)
		}
	}
	if f.r.count(otherAPI) != 1 {
		t.Fatal("second credential did not retain its own successful window")
	}
	held.open()
	if got := survivorReceive(t, first); got.result != f.prior || got.err != nil {
		t.Fatalf("first credential failed: %v", got.err)
	}
	f.authenticate(t)
	f.counts(t, 1, 0)
	f.clock.advance(30 * time.Second)
	f.authenticate(t)
	f.counts(t, 2, 0)
	if f.r.count(otherAPI) != 1 {
		t.Fatal("one credential refreshed another's state")
	}
}

func TestPrimaryWindowCanceledLeaderDoesNotCancelSharedCheck(t *testing.T) {
	f := newPrimaryWindowFixture(t, "personal")
	held := newSurvivorBarrier()
	defer held.open()
	var entered sync.Once
	f.r.recheck = func(ctx context.Context, _ *ResolveResult) (bool, error) {
		entered.Do(func() { close(held.entered) })
		select {
		case <-held.release:
			return true, nil
		case <-ctx.Done():
			return false, ctx.Err()
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	leader := survivorAuthenticate(f.a, ctx, f.api, f.secret)
	survivorWait(t, held.entered, "leader PRIMARY check")
	follower := survivorAuthenticate(f.a, context.Background(), f.api, f.secret)
	cancel()
	got := survivorReceive(t, leader) // PRIMARY is still held.
	if got.result != nil || !errors.Is(got.err, context.Canceled) {
		t.Fatalf("leader lost original cancellation: %v", got.err)
	}
	held.open()
	got = survivorReceive(t, follower)
	if got.result != f.prior || got.err != nil {
		t.Fatalf("live follower inherited canceled leader: %v", got.err)
	}
	f.authenticate(t)
	f.counts(t, 1, 0)
}

func TestPrimaryWindowWaitingCallerKeepsDeadline(t *testing.T) {
	f := newPrimaryWindowFixture(t, "personal")
	held := newSurvivorBarrier()
	defer held.open()
	var entered sync.Once
	f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
		entered.Do(func() { close(held.entered) })
		<-held.release
		return true, nil
	}
	leader := survivorAuthenticate(f.a, context.Background(), f.api, f.secret)
	survivorWait(t, held.entered, "shared PRIMARY check")
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	got := survivorReceive(t, survivorAuthenticate(f.a, ctx, f.api, f.secret))
	if got.result != nil || !errors.Is(got.err, context.DeadlineExceeded) {
		t.Fatalf("waiting caller lost original deadline: %v", got.err)
	}
	f.counts(t, 1, 0) // PRIMARY remains blocked; the caller must already be done.
	held.open()
	got = survivorReceive(t, leader)
	if got.result != f.prior || got.err != nil {
		t.Fatalf("expired waiter canceled live leader: %v", got.err)
	}
	f.authenticate(t)
	f.counts(t, 1, 0)
}

// Query time consumes the window; a slow positive must not grant a new full
// window from completion and thereby extend worst-case revocation latency.
func TestPrimaryWindowStartsAtPrimaryReadNotCompletion(t *testing.T) {
	f := newPrimaryWindowFixture(t, "personal")
	f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
		f.clock.advance(5 * time.Second)
		return true, nil
	}
	f.authenticate(t)
	f.clock.advance(25*time.Second - time.Nanosecond)
	f.authenticate(t)
	f.counts(t, 1, 0)
	f.clock.advance(time.Nanosecond)
	f.authenticate(t)
	f.counts(t, 2, 0)
}
