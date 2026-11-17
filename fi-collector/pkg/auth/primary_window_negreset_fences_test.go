package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

// Hold one real cold resolver invocation. Subsequent independent PRIMARY work
// can publish newer authority while that invocation still owes its negative.
type primaryNegresetColdResolver struct {
	*primaryWindowResolver
	held    *survivorBarrier
	started atomic.Bool
}

func (r *primaryNegresetColdResolver) resolveCold() (*ResolveResult, error) {
	if r.started.CompareAndSwap(false, true) {
		r.resolves.Add(1)
		r.held.stop()
		return nil, nil
	}
	return r.resolve()
}

func (r *primaryNegresetColdResolver) ValidateKey(context.Context, string, string) (*ResolveResult, error) {
	return r.resolveCold()
}

func (r *primaryNegresetColdResolver) ValidateGrant(_ context.Context, claims *GrantClaims) (*ResolveResult, error) {
	if !sameGrant(claims, r.result.Grant) {
		return nil, ErrUnauthenticated
	}
	return r.resolveCold()
}

// Exercise the same stamp -> real resolver -> publication sequence as a cold
// worker, without joining the intentionally held old singleflight. This is new
// PRIMARY evidence, not a manually seeded successful admission window.
func primaryNegresetColdPublication(t *testing.T, f *primaryWindowFixture) *cacheEntry {
	t.Helper()
	stamp, epoch, err := f.a.cache.validationStamp(context.Background(), f.key)
	if err != nil {
		t.Fatal(err)
	}
	result, err := f.a.resolveCredential(context.Background(), f.api, f.secret, f.prior.Grant)
	if err != nil || result != f.prior {
		t.Fatalf("new cold PRIMARY authority missing: %v", err)
	}
	published, err := f.a.cache.portPublishValidatedWithBudget(context.Background(), f.key, result, epoch, stamp, f.clock.now)
	if !published || err != nil {
		t.Fatalf("new cold PRIMARY publication failed: %v", err)
	}
	return primaryWindowResident(t, f)
}

// The unchanged-expired arm is a base red. The other arms are anti-regression
// controls: fixing that arm must not clear a replacement's budget, even after
// the replacement itself expires and only a handed-off reservation remains.
func TestPrimaryWindowLateNegativeResetsOnlyItsOwnBudget(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, source := range []string{"cached", "cold"} {
			for _, change := range []string{"unchanged-expired", "same-pointer-entry", "cold-handoff", "cold-handoff-expired", "new-budget-expired", "generation", "epoch"} {
				t.Run(kind+"/"+source+"/"+change, func(t *testing.T) {
					t.Parallel()
					f := newPrimaryColdWindowFixture(t, kind, time.Second)
					f.authenticate(t)
					f.authenticate(t)
					f.counts(t, 1, 1)
					old := primaryWindowResident(t, f)
					held := newSurvivorBarrier()
					defer held.open()
					done := make(chan survivorOutcome, 1)
					if source == "cold" {
						primaryNegresetExpire(t, f, old, true)
						f.a.keys = &primaryNegresetColdResolver{primaryWindowResolver: f.r, held: held}
						go func() {
							result, err := f.a.authenticateUncached(context.Background(), f.api, f.secret, f.key, old.result.Grant)
							done <- survivorOutcome{result, err}
						}()
					} else {
						f.clock.advance(30 * time.Second)
						f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
							if f.r.count(f.api) == 2 {
								held.stop()
								return false, nil
							}
							return true, nil
						}
						go func() {
							current, err := f.a.portCachedCredentialCurrent(context.Background(), f.api, f.secret, f.key, old.result)
							var result *ResolveResult
							if current {
								result = old.result
							}
							done <- survivorOutcome{result, err}
						}()
					}
					survivorWait(t, held.entered, "older PRIMARY negative")
					if source == "cached" {
						primaryNegresetExpire(t, f, old, true)
					}
					original := primaryNegresetRequireBudget(t, f)
					var resident *cacheEntry
					switch change {
					case "same-pointer-entry":
						resident = survivorPublish(t, f.a.cache, f.key, f.prior)
					case "cold-handoff", "cold-handoff-expired":
						f.prior = primaryWindowReplacement(f.prior)
						f.r.result = f.prior
						resident = primaryNegresetColdPublication(t, f)
						if primaryNegresetRequireBudget(t, f).id != original.id {
							t.Fatal("control requires successful TTL handoff of the same reservation")
						}
						if change == "cold-handoff-expired" {
							primaryNegresetExpire(t, f, resident, true)
							resident = nil
						}
					case "new-budget-expired":
						resident = survivorPublish(t, f.a.cache, f.key, f.prior)
						f.authenticate(t) // Different entry flight, completing before the old answer.
						if primaryNegresetRequireBudget(t, f).id == original.id {
							t.Fatal("control did not create a genuinely newer read budget")
						}
						primaryNegresetExpire(t, f, resident, true)
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
					f.a.cache.mu.Lock()
					budget := portBudgetLocked(f.a.cache, f.key)
					budgetBefore := budget.value
					generation, epoch := f.a.cache.generation.Load(), f.a.cache.generationFor(f.key).epoch
					var entryBefore cacheEntry
					if resident != nil {
						entryBefore = *resident
					}
					f.a.cache.mu.Unlock()
					held.open()
					got := survivorReceive(t, done)
					var changed *ProjectAuthChangedError
					fencedCold := source == "cold" && (change == "generation" || change == "epoch" || change == "cold-handoff-expired" || change == "new-budget-expired")
					if got.result != nil || (fencedCold && !errors.As(got.err, &changed)) || (!fencedCold && !errors.Is(got.err, ErrUnauthenticated)) {
						t.Fatalf("old negative changed refusal semantics: %v", got.err)
					}
					survivorResident(t, f.a.cache, f.key, resident)
					f.a.cache.mu.Lock()
					remaining := portBudgetLocked(f.a.cache, f.key)
					unchanged := remaining.id == budget.id && remaining.value == budgetBefore
					fencesUnchanged := f.a.cache.generation.Load() == generation && f.a.cache.generationFor(f.key).epoch == epoch
					entryUnchanged := resident == nil || *resident == entryBefore
					f.a.cache.mu.Unlock()
					if !fencesUnchanged || !entryUnchanged {
						t.Fatal("late negative mutated newer authority or its fences")
					}
					if change == "unchanged-expired" {
						if remaining.id != 0 {
							t.Fatal("late negative retained its own unsuperseded expired-entry budget")
						}
					} else if !unchanged {
						t.Fatal("late negative deleted or rewrote a newer/fenced PRIMARY budget")
					}
				})
			}
		}
	}
}
