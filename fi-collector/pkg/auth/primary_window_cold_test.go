package auth

import (
	"context"
	"testing"
	"time"
)

// Unlike the resident-window fixtures, this starts with a genuinely empty cache
// and reaches publication only through Authenticate and the counting resolver.
func newPrimaryColdWindowFixture(t *testing.T, kind string, ttl time.Duration) *primaryWindowFixture {
	t.Helper()
	api, secret, prior := survivorCredential(t, kind)
	prior.APIKeyDigest = APIKeyDigest(api)
	r := &primaryWindowResolver{result: prior, checks: make(map[string]int)}
	a, err := NewWithResolver(Config{CacheTTL: ttl, Peppers: mustPeppers(t, goldenPepper)}, r, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.cache.flush)
	clock := &primaryWindowClock{at: time.Now()}
	a.clock = clock.now
	return &primaryWindowFixture{a: a, r: r, clock: clock, api: api, secret: secret, key: CacheKey(api, secret), prior: prior}
}

func primaryWindowResident(t *testing.T, f *primaryWindowFixture) *cacheEntry {
	t.Helper()
	entry, status := f.a.cache.peek(f.key)
	if status != "fresh" || entry.result != f.prior {
		t.Fatal("Authenticate did not publish the exact newly resolved authority")
	}
	return entry
}

func primaryWindowWaitUntil(at time.Time) {
	timer := time.NewTimer(time.Until(at))
	defer timer.Stop()
	<-timer.C
}

func primaryWindowBurst(t *testing.T, f *primaryWindowFixture) {
	t.Helper()
	const n = 32
	start := make(chan struct{})
	done := make(chan survivorOutcome, n)
	for i := 0; i < n; i++ {
		go func() {
			<-start
			got, err := f.a.Authenticate(context.Background(), f.api, f.secret)
			done <- survivorOutcome{got, err}
		}()
	}
	close(start)
	for i := 0; i < n; i++ {
		got := survivorReceive(t, done)
		if got.result != f.prior || got.err != nil {
			t.Fatalf("burst did not return the new PRIMARY authority: %v", got.err)
		}
	}
}

func primaryWindowReplacement(prior *ResolveResult) *ResolveResult {
	return &ResolveResult{
		OrgID: prior.OrgID, WorkspaceID: prior.WorkspaceID, UserID: prior.UserID,
		KeyType: prior.KeyType, Principal: prior.Principal, Grant: prior.Grant,
		APIKeyDigest: prior.APIKeyDigest, SimulationAgent: prior.SimulationAgent,
		Projects: map[string]string{},
	}
}

func TestPrimaryWindowColdToFirstHitAccounting(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryColdWindowFixture(t, kind, 0)
			survivorResident(t, f.a.cache, f.key, nil)
			f.authenticate(t)
			f.counts(t, 0, 1)
			entry := primaryWindowResident(t, f)
			// Preserve the historical first-fresh-hit revocation fence: initial
			// cold validation is not itself a cached RevalidateCached operation.
			primaryWindowBurst(t, f)
			f.counts(t, 1, 1)
			primaryWindowBurst(t, f)
			f.counts(t, 1, 1)
			survivorResident(t, f.a.cache, f.key, entry)
		})
	}
}

// PW-SOL-01: cold at t=0, first cached check near TTL, ordinary expiration,
// genuine cold republication, then a concurrent burst inside the original
// revalidation window. No eviction, manual publication or fetchedAt mutation.
func TestPrimaryWindowTTLReplacementKeepsCredentialBudget(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, size := range []string{"default", "short"} {
			t.Run(kind+"/"+size, func(t *testing.T) {
				t.Parallel()
				ttl, late := 30*time.Second, 29*time.Second
				if size == "short" {
					ttl, late = time.Second, 500*time.Millisecond
				}
				f := newPrimaryColdWindowFixture(t, kind, ttl)
				f.authenticate(t)
				f.counts(t, 0, 1)
				old := primaryWindowResident(t, f)
				old.result.SetProjects(map[string]string{"old-only": "old-project"})
				primaryWindowWaitUntil(old.fetchedAt.Add(late))
				f.clock.advance(late)
				f.authenticate(t)
				f.counts(t, 1, 1)
				checkStarted := f.clock.now()
				primaryWindowWaitUntil(old.fetchedAt.Add(ttl + 10*time.Millisecond))
				f.clock.advance(ttl + 10*time.Millisecond - late)
				if _, status := f.a.cache.get(f.key); status != "miss" {
					t.Fatal("the original entry must actually expire before cold resolution")
				}
				survivorResident(t, f.a.cache, f.key, nil)
				f.prior = primaryWindowReplacement(f.prior)
				f.r.result = f.prior
				beforeCold := time.Now()
				f.authenticate(t)
				f.counts(t, 1, 2)
				replacement := primaryWindowResident(t, f)
				if replacement == old || replacement.fetchedAt.Before(beforeCold) {
					t.Fatal("cold resolution reused the old entry or its authority age")
				}
				if _, ok := f.prior.GetProject("old-only"); ok {
					t.Fatal("expired authority's project map survived cold resolution")
				}
				primaryWindowBurst(t, f)
				f.counts(t, 1, 2) // Base performs a second cached check here.
				// The cold lookup must not slide the original revalidation window.
				f.clock.advance(checkStarted.Add(30*time.Second - time.Nanosecond).Sub(f.clock.now()))
				f.authenticate(t)
				f.counts(t, 1, 2)
				f.clock.advance(time.Nanosecond)
				f.authenticate(t)
				f.counts(t, 2, 2)
				survivorResident(t, f.a.cache, f.key, replacement)
			})
		}
	}
}

func TestPrimaryWindowRepeatedShortTTLDoesNotResetBudget(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryColdWindowFixture(t, kind, time.Second)
			f.authenticate(t)
			f.authenticate(t)
			f.counts(t, 1, 1)
			for cold := int32(2); cold <= 4; cold++ {
				old := primaryWindowResident(t, f)
				primaryWindowWaitUntil(old.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
				f.clock.advance(time.Second + 10*time.Millisecond)
				f.prior = primaryWindowReplacement(f.prior)
				f.r.result = f.prior
				f.authenticate(t)
				primaryWindowBurst(t, f)
				f.counts(t, 1, cold)
			}
		})
	}
}
