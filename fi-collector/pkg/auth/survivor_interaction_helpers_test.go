package auth

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// The probe observes only the original caller. WithoutCancel deliberately hides
// it from the detached worker. These numbered boundaries pin the production
// call sequence, not scheduling: a changed sequence must fail the barrier or its
// cache/call-count preconditions, rather than silently testing an earlier wait.
type survivorContextProbe struct {
	context.Context
	errCalls  atomic.Int32
	doneCalls atomic.Int32
	beforeErr func(int32)
	onDone    func(int32)
}

func (p *survivorContextProbe) Err() error {
	n := p.errCalls.Add(1)
	if p.beforeErr != nil {
		p.beforeErr(n)
	}
	return p.Context.Err()
}

func (p *survivorContextProbe) Done() <-chan struct{} {
	n := p.doneCalls.Add(1)
	if p.onDone != nil {
		p.onDone(n)
	}
	return p.Context.Done()
}

type survivorBarrier struct {
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func newSurvivorBarrier() *survivorBarrier {
	return &survivorBarrier{entered: make(chan struct{}), release: make(chan struct{})}
}

func (b *survivorBarrier) stop() {
	close(b.entered)
	<-b.release
}

func (b *survivorBarrier) open() { b.once.Do(func() { close(b.release) }) }

func survivorWait(t *testing.T, ch <-chan struct{}, boundary string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(2 * time.Second): // Existing survivor cancellation bound.
		t.Fatalf("did not reach %s", boundary)
	}
}

type survivorOutcome struct {
	result *ResolveResult
	err    error
}

func survivorAuthenticate(a *Authenticator, ctx context.Context, api, token string) <-chan survivorOutcome {
	done := make(chan survivorOutcome, 1)
	go func() {
		r, err := a.Authenticate(ctx, api, token)
		done <- survivorOutcome{r, err}
		close(done)
	}()
	return done
}

func survivorReceive(t *testing.T, done <-chan survivorOutcome) survivorOutcome {
	t.Helper()
	select {
	case got := <-done:
		return got
	case <-time.After(2 * time.Second):
		t.Fatal("authentication did not settle within the existing cancellation bound")
		return survivorOutcome{}
	}
}

type survivorGrantResolver struct {
	publicationFunc
	grant func(context.Context, *GrantClaims) (*ResolveResult, error)
}

func (r survivorGrantResolver) ValidateGrant(ctx context.Context, claims *GrantClaims) (*ResolveResult, error) {
	return r.grant(ctx, claims)
}

func survivorCredential(t *testing.T, kind string) (string, string, *ResolveResult) {
	t.Helper()
	result := refreshResult()
	result.Principal = PrincipalPersonal
	if kind == "personal" {
		return "survivor-key", "survivor-secret", result
	}
	fields := sandboxFields(time.Now())
	api, token := "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
	claims, err := VerifyCollectorGrant(api, token, mustPeppers(t, goldenPepper), []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	result.OrgID, result.WorkspaceID, result.UserID = claims.Org, claims.Workspace, claims.User
	result.Grant, result.Principal = claims, PrincipalSandbox
	return api, token, result
}

func survivorPublish(t *testing.T, c *cache, key string, result *ResolveResult) *cacheEntry {
	t.Helper()
	stamp, epoch, err := c.validationStamp(context.Background(), key)
	if err != nil {
		t.Fatal(err)
	}
	ok, err := c.publishValidated(context.Background(), key, result, epoch, stamp)
	if !ok || err != nil {
		t.Fatalf("production publication control: published=%v err=%v", ok, err)
	}
	entry, status := c.peek(key)
	if status != "fresh" || entry.result != result || !entry.fetchedAt.Equal(stamp.started) {
		t.Fatal("publication did not preserve the exact result and pre-query timestamp")
	}
	return entry
}

func survivorResident(t *testing.T, c *cache, key string, expected *cacheEntry) {
	t.Helper()
	raw, exists := c.m.Load(key)
	if expected == nil {
		if exists {
			t.Fatal("waiter published a result for the canceled miss")
		}
		return
	}
	if !exists || raw != expected || !expected.fresh(c.ttl) {
		t.Fatal("valid resident control was removed, replaced or expired")
	}
}
