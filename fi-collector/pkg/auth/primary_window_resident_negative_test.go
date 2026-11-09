package auth

import (
	"context"
	"errors"
	"testing"
	"time"
)

// Both callers enter through Authenticate. The first pauses AFTER its expired
// cache observation but BEFORE validationStamp; the second completes a genuine
// positive cold handoff. No cache publication or eligibility metadata is seeded.
type primaryResidentNegativeScenario struct {
	f                       *primaryWindowFixture
	entry                   *cacheEntry
	budget                  portBudgetObservation
	beforeStamp, afterStamp *survivorBarrier
	done                    <-chan survivorOutcome
}

func primaryResidentNegativeSetup(t *testing.T, kind, size string) *primaryResidentNegativeScenario {
	t.Helper()
	ttl, late := time.Second, 500*time.Millisecond
	if size == "default" {
		ttl, late = 30*time.Second, 29*time.Second
	}
	f := newPrimaryColdWindowFixture(t, kind, ttl)
	f.authenticate(t)
	old := primaryWindowResident(t, f)
	primaryWindowWaitUntil(old.fetchedAt.Add(late))
	f.clock.advance(late)
	f.authenticate(t)
	f.counts(t, 1, 1)
	budget := primaryNegresetRequireBudget(t, f)
	primaryWindowWaitUntil(old.fetchedAt.Add(ttl + 10*time.Millisecond))
	f.clock.advance(ttl + 10*time.Millisecond - late)
	if old.fresh(f.a.cache.ttl) {
		t.Fatal("control requires actual TTL expiry without editing fetchedAt")
	}

	before, after := newSurvivorBarrier(), newSurvivorBarrier()
	probe := &survivorContextProbe{Context: context.Background(), beforeErr: func(n int32) {
		switch n {
		case 3: // Authenticate + credentialDeadline precede LockContext's first Err.
			before.stop() // The cache miss happened; the stamp gate is not held yet.
		case 6: // validationStamp has returned, before cold DoChan registration.
			after.stop()
		}
	}}
	done := survivorAuthenticate(f.a, probe, f.api, f.secret)
	t.Cleanup(func() {
		before.open()
		after.open()
		select {
		case <-done: // Also safe after the test consumed the closed result channel.
		case <-time.After(2 * time.Second):
			t.Error("delayed miss did not finish after barrier cleanup")
		}
	})
	survivorWait(t, before.entered, "miss before validation stamp")
	if len(f.a.cache.mu.held) != 0 {
		t.Fatal("miss barrier must precede cache-gate acquisition")
	}
	survivorResident(t, f.a.cache, f.key, nil) // The delayed caller removed E0.
	f.counts(t, 1, 1)                          // It has not queried PRIMARY yet.

	f.prior = primaryWindowReplacement(f.prior)
	f.r.result = f.prior
	f.authenticate(t) // Completed cold flight, not an in-flight result to join.
	f.counts(t, 1, 2)
	entry := primaryWindowResident(t, f)
	f.a.cache.mu.Lock()
	currentBudget := portBudgetLocked(f.a.cache, f.key)
	inherited := currentBudget.id == budget.id && budget.matchesEntryStart(entry) &&
		entry.portEligibilityCurrent(f.clock.now(), f.a.cache.generation.Load(), f.a.cache.generationFor(f.key).epoch)
	f.a.cache.mu.Unlock()
	if entry == old || !inherited {
		t.Fatal("control requires a fresh entry carrying the original successful window")
	}
	return &primaryResidentNegativeScenario{f, entry, budget, before, after, done}
}

func (s *primaryResidentNegativeScenario) snapshot(t *testing.T) {
	t.Helper()
	s.beforeStamp.open()
	survivorWait(t, s.afterStamp.entered, "cold lookup after handoff snapshot")
	if len(s.f.a.cache.mu.held) != 0 {
		t.Fatal("snapshot barrier must follow release of the cache gate")
	}
	survivorResident(t, s.f.a.cache, s.f.key, s.entry)
	s.f.counts(t, 1, 2)
}

// PW-NEGRESET-01: E1 is older than the negative's own snapshot, not a newer
// replacement to protect. The very next request must refuse while PRIMARY stays
// revoked, even though E1 would otherwise retain almost the entire old window.
func TestPrimaryWindowColdNegativeRejectsObservedResident(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, size := range []string{"default", "short"} {
			t.Run(kind+"/"+size, func(t *testing.T) {
				t.Parallel()
				s := primaryResidentNegativeSetup(t, kind, size)
				f := s.f
				s.snapshot(t)
				f.r.revoked.Store(true)
				s.afterStamp.open()
				got := survivorReceive(t, s.done)
				if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) {
					t.Fatalf("delayed cold PRIMARY negative did not refuse: %v", got.err)
				}
				f.counts(t, 1, 3)
				if !s.entry.fresh(f.a.cache.ttl) {
					t.Fatal("control expired before the post-negative request")
				}
				f.refused(t, ErrUnauthenticated) // Old production serves E1 without PRIMARY.
				if reads := f.r.count(f.api) + int(f.r.resolves.Load()); reads != 5 {
					t.Fatalf("next request must obtain new PRIMARY evidence: reads=%d want=5", reads)
				}
				if primaryNegresetBudget(f).id != 0 {
					t.Fatal("conclusive negatives retained a reusable budget")
				}
				survivorResident(t, f.a.cache, f.key, nil)
			})
		}
	}
}

// Opposite ordering: once the delayed miss has captured E1, a later positive
// publication or cached read supersedes its observation. Keep both the exact
// resident metadata and the full budget value unchanged by the late negative.
func TestPrimaryWindowColdNegativePreservesSupersedingResident(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, newer := range []string{"cold-publication", "cached-read"} {
			t.Run(kind+"/"+newer, func(t *testing.T) {
				t.Parallel()
				s := primaryResidentNegativeSetup(t, kind, "short")
				f := s.f
				s.snapshot(t)
				switch newer {
				case "cold-publication":
					f.prior = primaryWindowReplacement(f.prior)
					f.r.result = f.prior
					// Model another delayed miss using the real cold worker. A has
					// not registered its flight, so this positive completes first.
					got, err := f.a.authenticateUncached(context.Background(), f.api, f.secret, f.key, f.prior.Grant)
					if got != f.prior || err != nil {
						t.Fatalf("superseding cold authority did not publish: %v", err)
					}
				case "cached-read":
					f.clock.advance(30 * time.Second)
					f.authenticate(t) // Same entry, genuinely new successful budget.
				}
				entry := primaryWindowResident(t, f)
				f.a.cache.mu.Lock()
				budget := portBudgetLocked(f.a.cache, f.key)
				entryBefore, budgetBefore := *entry, budget.value
				generation, epoch := f.a.cache.generation.Load(), f.a.cache.generationFor(f.key).epoch
				f.a.cache.mu.Unlock()
				if newer == "cold-publication" && (entry == s.entry || budget.id != s.budget.id) {
					t.Fatal("control requires newer authority with the same carried reservation")
				}
				if newer == "cached-read" && (entry != s.entry || budget.id == s.budget.id) {
					t.Fatal("control requires the same entry with a newer successful reservation")
				}
				checks, resolves := f.r.count(f.api), f.r.resolves.Load()
				f.r.revoked.Store(true)
				s.afterStamp.open()
				got := survivorReceive(t, s.done)
				if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) {
					t.Fatalf("old cold negative changed refusal semantics: %v", got.err)
				}
				f.counts(t, checks, resolves+1)
				survivorResident(t, f.a.cache, f.key, entry)
				f.a.cache.mu.Lock()
				unchanged := portBudgetLocked(f.a.cache, f.key).id == budget.id && portBudgetLocked(f.a.cache, f.key).value == budgetBefore && *entry == entryBefore &&
					f.a.cache.generation.Load() == generation && f.a.cache.generationFor(f.key).epoch == epoch
				f.a.cache.mu.Unlock()
				if !unchanged {
					t.Fatal("old negative changed superseding authority, budget or fences")
				}
				f.authenticate(t) // The newer successful window remains independently usable.
				f.counts(t, checks, resolves+1)
			})
		}
	}
}
