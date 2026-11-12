package mcp

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// admissionFakeLeases models an atomic backend; it is not a distributed store.
type admissionFakeLeases struct {
	mu                                  sync.Mutex
	now                                 time.Time
	rows                                map[string]admissionFakeLease
	failAcquire, failRenew, failRelease bool
}
type admissionFakeLease struct {
	request LeaseRequest
	lease   SessionLease
}

func newAdmissionFakeLeases() *admissionFakeLeases {
	return &admissionFakeLeases{now: time.Now(), rows: map[string]admissionFakeLease{}}
}
func (f *admissionFakeLeases) sweep() {
	for id, row := range f.rows {
		if !f.now.Before(row.lease.ExpiresAt) || !f.now.Before(row.lease.IdleDeadline) || !f.now.Before(row.lease.AbsoluteDeadline) {
			delete(f.rows, id)
		}
	}
}
func (f *admissionFakeLeases) Acquire(_ context.Context, r LeaseRequest) (SessionLease, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sweep()
	if f.failAcquire {
		return SessionLease{}, errors.New("private backend details")
	}
	if row, ok := f.rows[r.ID]; ok {
		if row.request != r {
			return SessionLease{}, errAdmissionDenied
		}
		row.lease.ServerTime = f.now
		f.rows[r.ID] = row
		return row.lease, nil
	}
	org, workspace, session := 0, 0, 0
	for _, row := range f.rows {
		if row.request.Purpose != r.Purpose {
			continue
		}
		if row.request.Scope.OrgID == r.Scope.OrgID {
			org++
			if row.request.Scope == r.Scope {
				workspace++
				if row.request.SessionHash == r.SessionHash {
					session++
				}
			}
		}
	}
	if org >= r.Limits.Org || workspace >= r.Limits.Workspace || (r.Limits.Session > 0 && session >= r.Limits.Session) {
		return SessionLease{}, errAdmissionLimit
	}
	l := SessionLease{ID: r.ID, ServerTime: f.now, ExpiresAt: f.now.Add(r.TTL), IdleDeadline: f.now.Add(r.Idle), AbsoluteDeadline: f.now.Add(r.Absolute)}
	f.rows[r.ID] = admissionFakeLease{r, l}
	return l, nil
}
func (f *admissionFakeLeases) Renew(_ context.Context, id string, activity bool) (SessionLease, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sweep()
	if f.failRenew {
		return SessionLease{}, errors.New("private backend details")
	}
	row, ok := f.rows[id]
	if !ok {
		return SessionLease{}, errAdmissionDenied
	}
	if activity {
		row.lease.IdleDeadline = minAdmissionTime(f.now.Add(row.request.Idle), row.lease.AbsoluteDeadline)
	}
	row.lease.ServerTime = f.now
	row.lease.ExpiresAt = minAdmissionTime(f.now.Add(row.request.TTL), minAdmissionTime(row.lease.IdleDeadline, row.lease.AbsoluteDeadline))
	f.rows[id] = row
	return row.lease, nil
}
func (f *admissionFakeLeases) Release(_ context.Context, id string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failRelease {
		return errors.New("private backend details")
	}
	delete(f.rows, id)
	return nil
}
func (f *admissionFakeLeases) advance(d time.Duration) {
	f.mu.Lock()
	f.now = f.now.Add(d)
	f.mu.Unlock()
}
func admissionTestLeaseRequest(id string) LeaseRequest {
	r := admissionTestRequest()
	return LeaseRequest{ID: id, Scope: r.Scope, Actor: r.Actor, SessionHash: "session-a", Purpose: LeaseSession, Limits: LeaseLimits{Org: 2, Workspace: 1}, TTL: 3 * time.Second, Idle: 5 * time.Second, Absolute: 10 * time.Second}
}
func TestAdmissionAtomicScopeQuotasAndIdempotence(t *testing.T) {
	f := newAdmissionFakeLeases()
	ctx := context.Background()
	r := admissionTestLeaseRequest("lease-a")
	if _, err := f.Acquire(ctx, r); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Acquire(ctx, r); err != nil || len(f.rows) != 1 {
		t.Fatal("idempotent acquire charged twice")
	}
	r.ID = "lease-b"
	if _, err := f.Acquire(ctx, r); err != errAdmissionLimit {
		t.Fatal("workspace cap not atomic")
	}
	r.Scope.WorkspaceID = "workspace-b"
	if _, err := f.Acquire(ctx, r); err != nil {
		t.Fatal("sibling workspace denied")
	}
	r.ID = "lease-c"
	r.Scope.WorkspaceID = "workspace-c"
	if _, err := f.Acquire(ctx, r); err != errAdmissionLimit {
		t.Fatal("org cap bypassed")
	}
	r.Scope.OrgID = "org-b"
	if _, err := f.Acquire(ctx, r); err != nil {
		t.Fatal("sibling org denied")
	}
	if err := f.Release(ctx, "lease-a"); err != nil {
		t.Fatal(err)
	}
	if err := f.Release(ctx, "lease-a"); err != nil {
		t.Fatal("release not idempotent")
	}
}
func TestAdmissionConcurrentAcquireAndProcessLoss(t *testing.T) {
	f := newAdmissionFakeLeases()
	var wg sync.WaitGroup
	var mu sync.Mutex
	accepted := 0
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r := admissionTestLeaseRequest(string(rune('a' + i)))
			if _, err := f.Acquire(context.Background(), r); err == nil {
				mu.Lock()
				accepted++
				mu.Unlock()
			}
		}(i)
	}
	wg.Wait()
	if accepted != 1 {
		t.Fatal("concurrent workspace oversubscription")
	}
	f.advance(3 * time.Second)
	if _, err := f.Acquire(context.Background(), admissionTestLeaseRequest("replacement")); err != nil {
		t.Fatal("process-loss TTL did not reclaim")
	}
}
func TestAdmissionIdleAndAbsoluteCannotRenewForever(t *testing.T) {
	for _, activity := range []bool{false, true} {
		f := newAdmissionFakeLeases()
		r := admissionTestLeaseRequest("lease-a")
		_, _ = f.Acquire(context.Background(), r)
		until := 5
		if activity {
			until = 10
		}
		for i := 1; i < until; i++ {
			f.advance(time.Second)
			if _, err := f.Renew(context.Background(), r.ID, activity); err != nil {
				t.Fatal("early expiry")
			}
		}
		f.advance(time.Second)
		if _, err := f.Renew(context.Background(), r.ID, activity); err == nil {
			t.Fatal("lifetime was renewed forever")
		}
	}
}
func TestAdmissionLeaseFailuresAreClosedAndValueFree(t *testing.T) {
	f := newAdmissionFakeLeases()
	r := admissionTestLeaseRequest("lease-a")
	if _, err := acquireAdmissionLease(context.Background(), nil, r, f.now); err != errAdmissionUnavailable {
		t.Fatal("nil lease backend admitted")
	}
	f.failAcquire = true
	if _, err := acquireAdmissionLease(context.Background(), f, r, f.now); err != errAdmissionUnavailable {
		t.Fatal("acquire failure not sanitized")
	}
	f.failAcquire = false
	lease, err := acquireAdmissionLease(context.Background(), f, r, f.now)
	if err != nil {
		t.Fatal(err)
	}
	f.failRenew = true
	if err := lease.renew(context.Background(), false, f.now); err != errAdmissionUnavailable {
		t.Fatal("renew failure not closed")
	}
	f.failRelease = true
	if err := lease.close(); err != errAdmissionUnavailable {
		t.Fatal("release failure hidden")
	}
	f.advance(3 * time.Second)
	f.failAcquire = false
	if _, err := f.Acquire(context.Background(), admissionTestLeaseRequest("replacement")); err != nil {
		t.Fatal("failed release not reclaimed by TTL")
	}
}
func TestAdmissionSessionBindingUniformRefusal(t *testing.T) {
	r := admissionTestRequest()
	rec := admissionRecord{Version: 3, Scope: r.Scope, Actor: r.Actor, Revision: NewMembershipRevision([]byte("revision-a")), Target: r.Target}
	if !rec.matches(r, NewMembershipRevision([]byte("revision-a"))) {
		t.Fatal("own binding refused")
	}
	for _, mutate := range []func(*LiveAuthorizationRequest){func(r *LiveAuthorizationRequest) { r.Scope.WorkspaceID = "workspace-b" }, func(r *LiveAuthorizationRequest) { r.Scope.OrgID = "org-b" }, func(r *LiveAuthorizationRequest) { r.Actor.PersonID = "person-b" }, func(r *LiveAuthorizationRequest) { r.Actor.KeyID = "key-b" }, func(r *LiveAuthorizationRequest) { r.Actor.Kind = PrincipalAdmin }} {
		other := r
		mutate(&other)
		if rec.matches(other, NewMembershipRevision([]byte("revision-a"))) {
			t.Fatal("foreign binding accepted")
		}
	}
	if rec.matches(r, NewMembershipRevision([]byte("revision-b"))) {
		t.Fatal("old revision accepted")
	}
	rec.Version = 1
	if rec.matches(r, NewMembershipRevision([]byte("revision-a"))) {
		t.Fatal("legacy record accepted")
	}
}
