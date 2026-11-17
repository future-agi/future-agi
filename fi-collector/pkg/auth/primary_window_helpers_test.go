package auth

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// The existing authenticator clock isolates the revalidation window. Cache age
// and signed-grant expiry retain their production wall clock in these tests.
// Advancing this clock never mutates fetchedAt or relaxes the cache's 30s cap.
type primaryWindowClock struct {
	mu sync.Mutex
	at time.Time
}

func (c *primaryWindowClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *primaryWindowClock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.at = c.at.Add(d)
}

var primaryWindowOutage = errors.New("synthetic PRIMARY unavailable")

type primaryWindowResolver struct {
	publicationFunc // Only its project methods are used.
	mu              sync.Mutex
	checks          map[string]int
	resolves        atomic.Int32
	revoked         atomic.Bool
	outage          atomic.Bool
	result          *ResolveResult
	recheck         func(context.Context, *ResolveResult) (bool, error)
}

var _ interface {
	RevalidateCached(context.Context, string, string, *ResolveResult) (bool, error)
} = (*primaryWindowResolver)(nil)
var _ Resolver = (*primaryWindowResolver)(nil)

func (r *primaryWindowResolver) resolve() (*ResolveResult, error) {
	r.resolves.Add(1)
	if r.outage.Load() {
		return nil, primaryWindowOutage
	}
	if r.revoked.Load() {
		return nil, nil
	}
	return r.result, nil
}

func (r *primaryWindowResolver) ValidateKey(context.Context, string, string) (*ResolveResult, error) {
	return r.resolve()
}

func (r *primaryWindowResolver) ValidateGrant(_ context.Context, claims *GrantClaims) (*ResolveResult, error) {
	if !sameGrant(claims, r.result.Grant) {
		return nil, ErrUnauthenticated
	}
	return r.resolve()
}

func (r *primaryWindowResolver) RevalidateCached(ctx context.Context, api, _ string, prior *ResolveResult) (bool, error) {
	r.mu.Lock()
	r.checks[api]++
	r.mu.Unlock()
	if r.recheck != nil {
		return r.recheck(ctx, prior)
	}
	if r.outage.Load() {
		return false, primaryWindowOutage
	}
	return !r.revoked.Load(), nil
}

func (r *primaryWindowResolver) count(api string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.checks[api]
}

type primaryWindowFixture struct {
	a                *Authenticator
	r                *primaryWindowResolver
	clock            *primaryWindowClock
	api, secret, key string
	prior            *ResolveResult
	entry            *cacheEntry
}

func newPrimaryWindowFixture(t *testing.T, kind string) *primaryWindowFixture {
	t.Helper()
	api, secret, prior := survivorCredential(t, kind)
	prior.APIKeyDigest = APIKeyDigest(api)
	r := &primaryWindowResolver{result: prior, checks: make(map[string]int)}
	a, err := NewWithResolver(Config{Peppers: mustPeppers(t, goldenPepper)}, r, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	clock := &primaryWindowClock{at: time.Now()}
	a.clock = clock.now
	key := CacheKey(api, secret)
	return &primaryWindowFixture{a, r, clock, api, secret, key, prior, survivorPublish(t, a.cache, key, prior)}
}

func (f *primaryWindowFixture) authenticate(t *testing.T) {
	t.Helper()
	got, err := f.a.Authenticate(context.Background(), f.api, f.secret)
	if err != nil || got != f.prior {
		t.Fatalf("expected exact cached authority: result=%v err=%v", got == f.prior, err)
	}
}

func (f *primaryWindowFixture) counts(t *testing.T, checks int, resolves int32) {
	t.Helper()
	if got := f.r.count(f.api); got != checks || f.r.resolves.Load() != resolves {
		t.Fatalf("PRIMARY checks=%d cold reads=%d; want %d and %d", got, f.r.resolves.Load(), checks, resolves)
	}
}

func (f *primaryWindowFixture) refused(t *testing.T, want error) {
	t.Helper()
	got, err := f.a.Authenticate(context.Background(), f.api, f.secret)
	if got != nil || !errors.Is(err, want) {
		t.Fatalf("expected refusal %v: result=%v err=%v", want, got != nil, err)
	}
}
