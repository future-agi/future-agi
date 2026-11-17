package auth

import (
	"context"
	"errors"
	"testing"
	"time"
)

func primaryNegresetBudget(f *primaryWindowFixture) portBudgetObservation {
	f.a.cache.mu.Lock()
	defer f.a.cache.mu.Unlock()
	return portBudgetLocked(f.a.cache, f.key)
}

func primaryNegresetRequireBudget(t *testing.T, f *primaryWindowFixture) portBudgetObservation {
	t.Helper()
	budget := primaryNegresetBudget(f)
	if budget.id == 0 {
		t.Fatal("control requires an unexpired carried PRIMARY budget")
	}
	return budget
}

func primaryNegresetExpire(t *testing.T, f *primaryWindowFixture, entry *cacheEntry, remove bool) {
	t.Helper()
	primaryWindowWaitUntil(entry.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
	if entry.fresh(f.a.cache.ttl) {
		t.Fatal("control requires actual cache TTL expiry")
	}
	if remove {
		if _, status := f.a.cache.get(f.key); status != "miss" {
			t.Fatal("expired entry was not removed by the production read path")
		}
		survivorResident(t, f.a.cache, f.key, nil)
	}
	primaryNegresetRequireBudget(t, f)
}

// PW-REPLACEMENT-01: a denied cold lookup separates the previous successful
// window from recovery. The first fresh hit after recovery must read PRIMARY,
// even when the restored authority is identical to the pre-negative authority.
func TestPrimaryWindowColdNegativeResetsRecoveryFirstHit(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, size := range []string{"default", "short"} {
			for _, revokeAgain := range []bool{false, true} {
				outcome := "live"
				if revokeAgain {
					outcome = "revoked-again"
				}
				t.Run(kind+"/"+size+"/"+outcome, func(t *testing.T) {
					t.Parallel()
					ttl, late := 30*time.Second, 29*time.Second
					if size == "short" {
						ttl, late = time.Second, 500*time.Millisecond
					}
					f := newPrimaryColdWindowFixture(t, kind, ttl)
					f.authenticate(t)
					old := primaryWindowResident(t, f)
					primaryWindowWaitUntil(old.fetchedAt.Add(late))
					f.clock.advance(late)
					f.authenticate(t)
					f.counts(t, 1, 1)
					primaryNegresetExpire(t, f, old, true)
					f.clock.advance(ttl + 10*time.Millisecond - late)
					f.r.revoked.Store(true)
					f.refused(t, ErrUnauthenticated)
					f.counts(t, 1, 2)
					survivorResident(t, f.a.cache, f.key, nil)

					f.r.revoked.Store(false)
					f.prior = primaryWindowReplacement(f.prior)
					f.r.result = f.prior
					f.authenticate(t) // Recovery still requires its own positive cold read.
					f.counts(t, 1, 3)
					recovered := primaryWindowResident(t, f)
					if revokeAgain {
						f.r.revoked.Store(true)
						f.refused(t, ErrUnauthenticated)
						survivorResident(t, f.a.cache, f.key, nil)
					} else {
						f.authenticate(t)
						f.authenticate(t)
						survivorResident(t, f.a.cache, f.key, recovered)
					}
					f.counts(t, 2, 3) // Base skips this mandatory post-negative check.
				})
			}
		}
	}
}

// A cached PRIMARY answer can become negative after the entry expires. Both a
// lazily retained expired entry and an already removed one must lose its budget.
func TestPrimaryWindowExpiredCachedNegativeDropsBudget(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, removed := range []bool{false, true} {
			state := "expired-resident"
			if removed {
				state = "removed"
			}
			t.Run(kind+"/"+state, func(t *testing.T) {
				t.Parallel()
				f := newPrimaryColdWindowFixture(t, kind, time.Second)
				f.authenticate(t)
				old := primaryWindowResident(t, f)
				held := newSurvivorBarrier()
				defer held.open()
				f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
					if f.r.count(f.api) == 1 {
						held.stop()
						return false, nil
					}
					return true, nil
				}
				done := survivorAuthenticate(f.a, context.Background(), f.api, f.secret)
				survivorWait(t, held.entered, "negative cached check before TTL expiry")
				primaryNegresetExpire(t, f, old, removed)
				held.open()
				got := survivorReceive(t, done)
				if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) {
					t.Fatalf("expired cached negative did not refuse: %v", got.err)
				}
				f.counts(t, 1, 1)
				if primaryNegresetBudget(f).id != 0 {
					t.Fatal("expired cached negative left a carried budget for later recovery")
				}
				f.prior = primaryWindowReplacement(f.prior)
				f.r.result = f.prior
				f.authenticate(t)
				f.counts(t, 1, 2)
				f.authenticate(t)
				f.authenticate(t)
				f.counts(t, 2, 2)
			})
		}
	}
}

// Query-age rejection must not preserve a conclusive negative's old budget.
// Use the real publication gate and elapsed TTL, not an edited query timestamp.
func TestPrimaryWindowAgedColdNegativeDropsBudget(t *testing.T) {
	f := newPrimaryColdWindowFixture(t, "personal", time.Second)
	f.authenticate(t)
	f.authenticate(t)
	old := primaryWindowResident(t, f)
	primaryNegresetExpire(t, f, old, true)
	stamp, epoch, err := f.a.cache.validationStamp(context.Background(), f.key)
	if err != nil {
		t.Fatal(err)
	}
	f.r.revoked.Store(true)
	result, err := f.a.resolveCredential(context.Background(), f.api, f.secret, nil)
	if result != nil || err != nil {
		t.Fatalf("control requires conclusive cold PRIMARY rejection: %v", err)
	}
	primaryWindowWaitUntil(stamp.started.Add(f.a.cache.ttl + 10*time.Millisecond))
	published, err := f.a.cache.portPublishValidatedWithBudget(context.Background(), f.key, result, epoch, stamp, f.clock.now)
	if published || err != nil {
		t.Fatalf("aged publication fence changed: published=%v err=%v", published, err)
	}
	if primaryNegresetBudget(f).id != 0 {
		t.Fatal("query-age rejection retained a conclusively rejected budget")
	}
	f.r.revoked.Store(false)
	f.authenticate(t)
	f.authenticate(t)
	f.counts(t, 2, 3)
}
