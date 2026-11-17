package auth

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestPrimaryWindowExpiredBudgetCannotAuthorizeColdFailure(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, failure := range []string{"revoked", "outage"} {
			t.Run(kind+"/"+failure, func(t *testing.T) {
				t.Parallel()
				f := newPrimaryColdWindowFixture(t, kind, time.Second)
				f.authenticate(t)
				f.authenticate(t)
				f.counts(t, 1, 1)
				entry := primaryWindowResident(t, f)
				primaryWindowWaitUntil(entry.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
				want := ErrUnauthenticated
				if failure == "revoked" {
					f.r.revoked.Store(true)
				} else {
					f.r.outage.Store(true)
					want = primaryWindowOutage
				}
				for cold := int32(2); cold <= 3; cold++ {
					f.refused(t, want)
					f.counts(t, 1, cold)
					survivorResident(t, f.a.cache, f.key, nil)
				}
				// A record of prior reads is never recovery evidence: another
				// actual cold PRIMARY lookup is required before success.
				f.r.revoked.Store(false)
				f.r.outage.Store(false)
				f.prior = primaryWindowReplacement(f.prior)
				f.r.result = f.prior
				f.authenticate(t)
				f.counts(t, 1, 4)
			})
		}
	}
}

func TestPrimaryWindowColdBudgetDoesNotCrossAuthorityChange(t *testing.T) {
	changes := map[string]func(*ResolveResult){
		"organization": func(r *ResolveResult) { r.OrgID = "new-org" },
		"workspace":    func(r *ResolveResult) { r.WorkspaceID = "new-workspace" },
		"person":       func(r *ResolveResult) { r.UserID = "new-person" },
		"key-type":     func(r *ResolveResult) { r.KeyType = "new-type" },
		"principal":    func(r *ResolveResult) { r.Principal = PrincipalSandbox },
		"digest":       func(r *ResolveResult) { r.APIKeyDigest = APIKeyDigest("new-key") },
		"simulation":   func(r *ResolveResult) { r.SimulationAgent.ID = "new-agent" },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			f := newPrimaryColdWindowFixture(t, "personal", time.Second)
			f.authenticate(t)
			f.authenticate(t)
			f.counts(t, 1, 1)
			entry := primaryWindowResident(t, f)
			primaryWindowWaitUntil(entry.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
			f.prior = primaryWindowReplacement(f.prior)
			change(f.prior)
			f.r.result = f.prior
			f.authenticate(t)
			f.counts(t, 1, 2)
			primaryWindowBurst(t, f)
			f.counts(t, 2, 2)
		})
	}
}

func TestPrimaryWindowColdBudgetHonorsInvalidation(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, signal := range []string{"notification", "flush", "epoch"} {
			t.Run(kind+"/"+signal, func(t *testing.T) {
				t.Parallel()
				f := newPrimaryColdWindowFixture(t, kind, time.Second)
				f.authenticate(t)
				f.authenticate(t)
				entry := primaryWindowResident(t, f)
				primaryWindowWaitUntil(entry.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
				// Invalidate while only the read budget remains, not an entry.
				f.a.cache.get(f.key)
				survivorResident(t, f.a.cache, f.key, nil)
				switch signal {
				case "notification":
					payload := RevocationPrefix + APIKeyDigest(f.api)
					if kind == "grant" {
						payload = "grant:" + f.prior.Grant.ID
					}
					f.a.evictCredential(payload)
				case "flush":
					f.a.cache.flush()
				case "epoch":
					f.a.cache.mu.Lock()
					f.a.cache.generationFor(f.key).epoch++
					f.a.cache.mu.Unlock()
				}
				f.authenticate(t)
				f.counts(t, 1, 2)
				f.r.revoked.Store(true)
				// The new generation must not inherit the older read budget.
				f.refused(t, ErrUnauthenticated)
				f.counts(t, 2, 2)
				survivorResident(t, f.a.cache, f.key, nil)
			})
		}
	}
}

// A check can straddle TTL expiry. The later cold result supplies its own live
// PRIMARY authority, not the held check's eventual positive/negative/error.
func TestPrimaryWindowColdReplacementWhileCheckInFlight(t *testing.T) {
	for _, outcome := range []string{"positive", "negative", "outage"} {
		t.Run(outcome, func(t *testing.T) {
			t.Parallel()
			f := newPrimaryColdWindowFixture(t, "personal", time.Second)
			f.authenticate(t)
			old := primaryWindowResident(t, f)
			held := newSurvivorBarrier()
			defer held.open()
			f.r.recheck = func(context.Context, *ResolveResult) (bool, error) {
				if f.r.count(f.api) > 1 {
					return true, nil // Let a duplicate settle so its count fails explicitly.
				}
				held.stop()
				switch outcome {
				case "negative":
					return false, nil
				case "outage":
					return false, primaryWindowOutage
				}
				return true, nil
			}
			done := make(chan survivorOutcome, 1)
			go func() {
				current, err := f.a.portCachedCredentialCurrent(context.Background(), f.api, f.secret, f.key, old.result)
				var result *ResolveResult
				if current {
					result = old.result
				}
				done <- survivorOutcome{result, err}
			}()
			survivorWait(t, held.entered, "check crossing TTL")
			primaryWindowWaitUntil(old.fetchedAt.Add(f.a.cache.ttl + 10*time.Millisecond))
			f.prior = primaryWindowReplacement(f.prior)
			f.r.result = f.prior
			f.authenticate(t)
			f.counts(t, 1, 2)
			replacement := primaryWindowResident(t, f)
			primaryWindowBurst(t, f) // Must finish while the old check is held.
			f.counts(t, 1, 2)
			held.open()
			got := survivorReceive(t, done)
			var want error
			if outcome == "negative" {
				want = ErrUnauthenticated
			} else if outcome == "outage" {
				want = primaryWindowOutage
			}
			if got.result != nil || !errors.Is(got.err, want) {
				t.Fatalf("late check changed replacement admission: %v", got.err)
			}
			f.authenticate(t)
			f.counts(t, 1, 2)
			survivorResident(t, f.a.cache, f.key, replacement)
		})
	}
}
