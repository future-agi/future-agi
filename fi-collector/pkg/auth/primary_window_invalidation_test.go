package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

// (i), delivered signal: exercise the actual WatchRevocations dispatcher and
// all cache fences without a Redis socket. The next request must read PRIMARY
// despite the open successful window. No polling or extra request is allowed.
func TestPrimaryWindowDeliveredNoticeRefusesNextRequest(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, signal := range []string{"notification", "legacy", "direct", "flush", "generation", "epoch"} {
			t.Run(kind+"/"+signal, func(t *testing.T) {
				f := newPrimaryWindowFixture(t, kind)
				f.authenticate(t)
				f.clock.advance(time.Second)
				f.authenticate(t)
				f.counts(t, 1, 0) // Prove a real open window before the mutation.
				f.r.revoked.Store(true)
				switch signal {
				case "notification":
					payload := RevocationPrefix + APIKeyDigest(f.api)
					if f.prior.Grant != nil {
						payload = "grant:" + f.prior.Grant.ID
					}
					f.a.evictCredential(payload)
				case "legacy":
					f.a.evictCredential(f.key)
				case "direct":
					f.a.InvalidateKey(f.key)
				case "flush":
					f.a.cache.flush()
				case "generation":
					f.a.cache.mu.Lock()
					f.a.cache.generation.Add(1)
					f.a.cache.mu.Unlock()
				case "epoch":
					f.a.cache.mu.Lock()
					f.a.cache.generationFor(f.key).epoch++
					f.a.cache.mu.Unlock()
				}
				f.refused(t, ErrUnauthenticated) // The very next request, not an eventual retry.
				if signal == "generation" || signal == "epoch" {
					f.counts(t, 2, 0)
				} else {
					f.counts(t, 1, 1) // Eviction re-enters cold PRIMARY validation.
				}
				survivorResident(t, f.a.cache, f.key, nil)
			})
		}
	}
}

// (g): neither a positive nor a negative old flight may act on a replacement,
// even one with the same result pointer; each generation dimension also fences
// a resident entry without requiring its removal.
func TestPrimaryWindowLateAnswersCannotMutateNewAuthority(t *testing.T) {
	for _, live := range []bool{false, true} {
		for _, change := range []string{"replacement", "same-pointer-entry", "eviction", "generation", "epoch"} {
			t.Run(change+"/"+map[bool]string{false: "negative", true: "positive"}[live], func(t *testing.T) {
				f := newPrimaryWindowFixture(t, "personal")
				held := newSurvivorBarrier()
				defer held.open()
				var calls atomic.Int32
				f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
					if calls.Add(1) == 1 {
						held.stop()
						return live, nil
					}
					return true, nil
				}
				old := f.prior
				done := make(chan struct {
					current bool
					err     error
				}, 1)
				go func() {
					current, err := f.a.portCachedCredentialCurrent(context.Background(), f.api, f.secret, f.key, old)
					done <- struct {
						current bool
						err     error
					}{current, err}
				}()
				survivorWait(t, held.entered, "old PRIMARY revalidation")
				resident := f.entry
				switch change {
				case "replacement":
					f.prior = refreshResult()
					f.prior.APIKeyDigest = APIKeyDigest(f.api)
					resident = survivorPublish(t, f.a.cache, f.key, f.prior)
				case "same-pointer-entry":
					resident = survivorPublish(t, f.a.cache, f.key, old)
				case "eviction":
					f.a.InvalidateKey(f.key)
					resident = nil
				case "generation":
					f.a.cache.mu.Lock()
					f.a.cache.generation.Add(1)
					f.a.cache.mu.Unlock()
				case "epoch":
					f.a.cache.mu.Lock()
					f.a.cache.generationFor(f.key).epoch++
					f.a.cache.mu.Unlock()
				}
				held.open()
				select {
				case got := <-done:
					if got.current || (live && got.err != nil) || (!live && !errors.Is(got.err, ErrUnauthenticated)) {
						t.Fatalf("late answer admitted changed authority: current=%v err=%v", got.current, got.err)
					}
				case <-time.After(2 * time.Second):
					t.Fatal("late revalidation did not settle")
				}
				survivorResident(t, f.a.cache, f.key, resident)
				f.counts(t, 1, 0)
				if resident != nil {
					f.authenticate(t)
					f.authenticate(t)
					f.counts(t, 2, 0) // Old completion cannot validate the replacement's window.
					survivorResident(t, f.a.cache, f.key, resident)
				}
			})
		}
	}
}

func TestPrimaryWindowInvalidationDoesNotJoinOldFlight(t *testing.T) {
	for _, change := range []string{"generation", "epoch"} {
		t.Run(change, func(t *testing.T) {
			f := newPrimaryWindowFixture(t, "personal")
			held := newSurvivorBarrier()
			defer held.open()
			var calls atomic.Int32
			f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
				if calls.Add(1) == 1 {
					held.stop()
					return true, nil
				}
				return false, nil
			}
			old := survivorAuthenticate(f.a, context.Background(), f.api, f.secret)
			survivorWait(t, held.entered, "old generation's PRIMARY check")
			f.r.revoked.Store(true)
			f.a.cache.mu.Lock()
			if change == "generation" {
				f.a.cache.generation.Add(1)
			} else {
				f.a.cache.generationFor(f.key).epoch++
			}
			f.a.cache.mu.Unlock()
			next := survivorReceive(t, survivorAuthenticate(f.a, context.Background(), f.api, f.secret))
			if next.result != nil || !errors.Is(next.err, ErrUnauthenticated) {
				t.Fatalf("new generation joined stale flight: %v", next.err)
			}
			f.counts(t, 2, 0)
			held.open()
			got := survivorReceive(t, old)
			if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) {
				t.Fatalf("old positive survived revocation: %v", got.err)
			}
			f.counts(t, 2, 1)
			survivorResident(t, f.a.cache, f.key, nil)
		})
	}
}
