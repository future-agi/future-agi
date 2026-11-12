package auth

import (
	"context"
	"errors"
	"strconv"
	"testing"
	"time"
)

// (a), (b): literals deliberately pin the contract independently of the new
// production constant. No Go source change is required to compile these reds.
func TestPrimaryWindowOneCheckAndExactBoundary(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryWindowFixture(t, kind)
			fetchedAt := f.entry.fetchedAt
			for i := 0; i < 8; i++ {
				f.authenticate(t)
			}
			f.counts(t, 1, 0)
			f.clock.advance(30*time.Second - time.Nanosecond)
			f.authenticate(t)
			f.counts(t, 1, 0)
			f.clock.advance(time.Nanosecond)
			f.authenticate(t)
			f.counts(t, 2, 0)
			f.clock.advance(30*time.Second - time.Nanosecond)
			f.authenticate(t)
			f.counts(t, 2, 0)
			f.clock.advance(time.Nanosecond)
			f.authenticate(t)
			f.counts(t, 3, 0)
			survivorResident(t, f.a.cache, f.key, f.entry)
			if !f.entry.fetchedAt.Equal(fetchedAt) {
				t.Fatal("revalidation refreshed the cache's original query age")
			}
		})
	}
}

// (e): a cached negative evicts only this authority; it is not a negative cache.
func TestPrimaryWindowNegativeEvictsAndReresolves(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryWindowFixture(t, kind)
			f.authenticate(t)
			f.r.revoked.Store(true)
			f.clock.advance(30 * time.Second)
			f.refused(t, ErrUnauthenticated)
			f.counts(t, 2, 0)
			survivorResident(t, f.a.cache, f.key, nil)
			f.refused(t, ErrUnauthenticated)
			f.counts(t, 2, 1)
			f.r.revoked.Store(false)
			f.authenticate(t)
			f.counts(t, 2, 2)
			// Even the same result pointer in a new entry has no old window.
			f.authenticate(t)
			f.authenticate(t)
			f.counts(t, 3, 2)
		})
	}
}

// (f): failure never marks or renews success, either initially or after expiry.
func TestPrimaryWindowOutageNeverValidates(t *testing.T) {
	for _, initialSuccess := range []bool{false, true} {
		f := newPrimaryWindowFixture(t, "personal")
		want := 0
		if initialSuccess {
			f.authenticate(t)
			want++
			f.clock.advance(30 * time.Second)
		}
		f.r.outage.Store(true)
		for i := 0; i < 2; i++ {
			f.refused(t, primaryWindowOutage)
			want++
			f.counts(t, want, 0)
			survivorResident(t, f.a.cache, f.key, f.entry)
		}
		f.r.outage.Store(false)
		f.authenticate(t)
		want++
		f.authenticate(t)
		f.counts(t, want, 0)
	}
}

// (i), missed delivery: the last near-boundary hit cannot slide the window.
// The synthetic window clock keeps the real cache fresh to isolate this fence;
// the companion lifetime test separately pins the unchanged real cache cap.
func TestPrimaryWindowMissedNoticeBound(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			f := newPrimaryWindowFixture(t, kind)
			f.authenticate(t)
			f.clock.advance(time.Nanosecond)
			f.r.revoked.Store(true)
			f.authenticate(t) // A missed notice permits only the already-open window.
			f.counts(t, 1, 0)
			f.clock.advance(30*time.Second - 2*time.Nanosecond)
			f.authenticate(t)
			f.counts(t, 1, 0)
			f.clock.advance(time.Nanosecond)
			f.refused(t, ErrUnauthenticated)
			f.counts(t, 2, 0)
			survivorResident(t, f.a.cache, f.key, nil)
		})
	}
}

func TestPrimaryWindowDoesNotExtendCacheLifetime(t *testing.T) {
	f := newPrimaryWindowFixture(t, "personal")
	f.a.cache.ttl = 40 * time.Millisecond // A smaller configured lifetime, never a wider one.
	f.authenticate(t)
	f.authenticate(t)
	f.counts(t, 1, 0)
	f.r.revoked.Store(true)
	timer := time.NewTimer(time.Until(f.entry.fetchedAt.Add(f.a.cache.ttl)))
	defer timer.Stop()
	<-timer.C
	f.refused(t, ErrUnauthenticated)
	f.counts(t, 1, 1) // Expiry forces cold PRIMARY, even with the window clock frozen.
	survivorResident(t, f.a.cache, f.key, nil)
}

// (h): window hits must retain credentialDeadline, including caller cancellation.
func TestPrimaryWindowGrantExpiryAndCancellation(t *testing.T) {
	f := newPrimaryWindowFixture(t, "grant")
	// Re-sign an actually short-lived grant; do not mutate verified claims.
	fields := sandboxFields(time.Now())
	fields[8] = strconv.FormatInt(time.Now().Add(1500*time.Millisecond).Unix(), 10)
	f.secret = signedGrantFields(fields, goldenPepper)
	claims, err := VerifyCollectorGrant(f.api, f.secret, f.a.cfg.Peppers, []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	f.prior.Grant = claims
	f.authenticate(t)
	f.authenticate(t)
	f.counts(t, 1, 0)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := f.a.Authenticate(ctx, f.api, f.secret); got != nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("open window swallowed cancellation: %v", err)
	}
	survivorExpiryBoundary(t, claims.Exp)
	f.refused(t, ErrUnauthenticated)
	if ok, err := f.a.portCachedCredentialCurrent(context.Background(), f.api, f.secret, f.key, f.prior); ok || !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("window helper bypassed credentialDeadline: current=%v err=%v", ok, err)
	}
	f.counts(t, 1, 0)
}
