package auth

import (
	"testing"
	"time"
)

func TestPrimaryReadBudgetExpiresWithoutTraffic(t *testing.T) {
	t.Parallel()
	f := newPrimaryColdWindowFixture(t, "personal", 0)
	f.authenticate(t)
	f.authenticate(t)
	f.counts(t, 1, 1)
	started := f.clock.now() // The fixture clock is frozen at the exact PRIMARY start.
	f.a.cache.mu.Lock()
	budget := portBudgetLocked(f.a.cache, f.key)
	f.a.cache.mu.Unlock()
	if budget.id == 0 || !budget.startedAt(started) {
		t.Fatal("successful cached revalidation did not reserve its credential budget")
	}
	// Use the real production timer, not a shortened/reset timer or an expired
	// fake clock. No requests, eviction or cache maintenance drive this removal.
	deadline := time.NewTimer(32 * time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(10 * time.Millisecond)
	defer tick.Stop()
	for {
		f.a.cache.mu.Lock()
		remaining := portBudgetCountLocked(f.a.cache)
		releasedMap := portBudgetMapNilLocked(f.a.cache)
		f.a.cache.mu.Unlock()
		if remaining == 0 {
			if !releasedMap {
				t.Fatal("idle budget records vanished but empty map storage was retained")
			}
			if time.Since(started) < 30*time.Second {
				t.Fatal("idle budget expired before its original 30s read window")
			}
			break
		}
		select {
		case <-tick.C:
		case <-deadline.C:
			t.Fatal("idle credential retained a read-budget reservation beyond 30s plus scheduling margin")
		}
	}
	f.counts(t, 1, 1)
}

func TestPrimaryReadBudgetInvalidationRetainsNoUnknownKeys(t *testing.T) {
	c := newCache(30*time.Second, 0)
	for i := 0; i < 1000; i++ {
		c.invalidateKey(CacheKey(string(rune(i)), "synthetic-secret"))
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if portBudgetCountLocked(c) != 0 || len(c.generations) != 64 {
		t.Fatal("unknown-key invalidation allocated credential budget tombstones")
	}
}

func TestPrimaryWindowManualReplacementCannotInheritBudget(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryColdWindowFixture(t, kind, 0)
			f.authenticate(t)
			f.authenticate(t)
			f.counts(t, 1, 1)
			// This has no new resolver evidence. Neither a record in the budget
			// map nor reuse of the exact result pointer may authorize its hit.
			survivorPublish(t, f.a.cache, f.key, f.prior)
			f.r.revoked.Store(true)
			f.refused(t, ErrUnauthenticated)
			f.counts(t, 2, 1)
			survivorResident(t, f.a.cache, f.key, nil)
			f.a.cache.mu.Lock()
			remaining := portBudgetCountLocked(f.a.cache)
			f.a.cache.mu.Unlock()
			if remaining != 0 {
				t.Fatal("negative revalidation left the credential's old read budget")
			}
		})
	}
}

func TestPrimaryWindowColdBudgetBindsExactGrantClaims(t *testing.T) {
	f := newPrimaryColdWindowFixture(t, "grant", time.Second)
	f.authenticate(t)
	f.authenticate(t)
	f.counts(t, 1, 1)
	old := primaryWindowResident(t, f)
	primaryWindowWaitUntil(old.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
	fields := sandboxFields(time.Now())
	fields[13] = "55555555-5555-4555-8555-555555555555"
	f.secret = signedGrantFields(fields, goldenPepper)
	claims, err := VerifyCollectorGrant(f.api, f.secret, f.a.cfg.Peppers, []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if CacheKey(f.api, f.secret) != f.key || claims.ID != f.prior.Grant.ID || sameGrant(claims, f.prior.Grant) {
		t.Fatal("control requires the same grant id/cache key but different verified claims")
	}
	f.prior = primaryWindowReplacement(f.prior)
	// Keep all other authority fields equal, so only the full claim comparison
	// can reject the old budget. The injected resolver independently accepts the
	// new claims; production ValidateGrant additionally binds registry identity.
	f.prior.Grant = claims
	f.r.result = f.prior
	f.authenticate(t)
	f.counts(t, 1, 2)
	primaryWindowBurst(t, f)
	f.counts(t, 2, 2)
}
