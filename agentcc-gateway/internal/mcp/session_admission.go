package mcp

import (
	"context"
	"sync"
	"time"
)

type LeasePurpose uint8

const (
	LeaseSession LeasePurpose = iota + 1
	LeaseCall
	LeaseStream
)

type LeaseLimits struct{ Org, Workspace, Session int }

// LeaseCapacityExceeded is the adapter's value-free quota refusal. Other
// backend errors remain unavailable and are never reflected to clients.
type LeaseRefusal uint8

const LeaseCapacityExceeded LeaseRefusal = 1

func (LeaseRefusal) Error() string { return "mcp: lease capacity unavailable" }

// LeaseRequest is immutable for an ID. Acquire must atomically check BOTH
// org and workspace budgets (and Session when nonzero), then create exactly
// one lease. Reusing an ID with a different binding must fail. IDs are random
// opaque tokens, not user-selected idempotency keys.
type LeaseRequest struct {
	ID                  string
	Scope               ScopeKey
	Actor               AdmissionActor
	SessionHash         string
	Purpose             LeasePurpose
	Limits              LeaseLimits
	TTL, Idle, Absolute time.Duration
}

// ServerTime is read afresh for every response, including idempotent Acquire.
// Consumers derive remaining TTL from it, rather than comparing host clocks.
type SessionLease struct {
	ID                                                    string
	ServerTime, ExpiresAt, IdleDeadline, AbsoluteDeadline time.Time
}

// SessionLeaseStore is a required shared backend for hosted admission. The
// adapter must use authoritative server time, atomic quotas and TTL reclamation
// after process loss. Renew must not revive expired/released leases or extend
// their original absolute deadline. Only activity=true advances idle expiry.
// Release is idempotent. All methods must honor their context deadline. There
// is intentionally no process-local implementation as a production default.
type SessionLeaseStore interface {
	Acquire(context.Context, LeaseRequest) (SessionLease, error)
	Renew(ctx context.Context, id string, activity bool) (SessionLease, error)
	Release(ctx context.Context, id string) error
}

type admissionLease struct {
	mu          sync.Mutex
	store       SessionLeaseStore
	request     LeaseRequest
	value       SessionLease
	localExpiry time.Time
	closed      bool
	closeErr    error
}

func acquireAdmissionLease(ctx context.Context, store SessionLeaseStore, r LeaseRequest, now time.Time) (*admissionLease, error) {
	if store == nil {
		return nil, errAdmissionUnavailable
	}
	if !r.Scope.valid() || !r.Actor.valid() || !admissionOpaque(r.ID) || r.SessionHash == "" || r.Purpose < LeaseSession || r.Purpose > LeaseStream || r.Limits.Org <= 0 || r.Limits.Workspace <= 0 || r.Limits.Workspace > r.Limits.Org || r.Limits.Session < 0 || r.TTL <= 0 || r.TTL > 3*time.Second || r.Idle <= 0 || r.Absolute <= 0 || r.Idle > r.Absolute {
		return nil, errAdmissionDenied
	}
	ctx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	if ctx.Err() != nil {
		return nil, errAdmissionUnavailable
	}
	v, err := store.Acquire(ctx, r)
	if err != nil {
		// A timeout may have lost an acknowledgement after atomic acquisition.
		// Release by the original ID; TTL remains the fallback on store loss.
		_ = (&admissionLease{store: store, request: r}).close()
		if err == errAdmissionLimit || err == LeaseCapacityExceeded {
			return nil, errAdmissionLimit
		}
		return nil, errAdmissionUnavailable
	}
	l := &admissionLease{store: store, request: r, value: v, localExpiry: now.Add(v.ExpiresAt.Sub(v.ServerTime))}
	if ctx.Err() != nil || !validAdmissionLease(v, r.ID) || v.AbsoluteDeadline.After(v.ServerTime.Add(r.Absolute)) || v.IdleDeadline.After(v.ServerTime.Add(r.Idle)) || v.ExpiresAt.After(v.ServerTime.Add(r.TTL)) {
		// The request ID is the release key even for a malformed response.
		l.value.ID = r.ID
		_ = l.close()
		return nil, errAdmissionUnavailable
	}
	return l, nil
}
func validAdmissionLease(v SessionLease, id string) bool {
	return v.ID == id && !v.ServerTime.IsZero() && v.ServerTime.Before(v.ExpiresAt) && v.ServerTime.Before(v.IdleDeadline) && v.ServerTime.Before(v.AbsoluteDeadline) && !v.ExpiresAt.After(v.IdleDeadline) && !v.ExpiresAt.After(v.AbsoluteDeadline) && !v.IdleDeadline.After(v.AbsoluteDeadline)
}
func (l *admissionLease) renew(ctx context.Context, activity bool, now time.Time) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.closed || !now.Before(l.localExpiry) {
		return errAdmissionDenied
	}
	ctx, cancel := context.WithTimeout(ctx, admissionLookupTimeout)
	defer cancel()
	if ctx.Err() != nil {
		return errAdmissionUnavailable
	}
	v, err := l.store.Renew(ctx, l.request.ID, activity)
	if err != nil || ctx.Err() != nil || !validAdmissionLease(v, l.request.ID) || v.ServerTime.Before(l.value.ServerTime) || !v.AbsoluteDeadline.Equal(l.value.AbsoluteDeadline) || v.IdleDeadline.After(v.ServerTime.Add(l.request.Idle)) || v.ExpiresAt.After(v.ServerTime.Add(l.request.TTL)) {
		return errAdmissionUnavailable
	}
	l.value = v
	l.localExpiry = now.Add(v.ExpiresAt.Sub(v.ServerTime))
	return nil
}
func (l *admissionLease) close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.closed {
		return l.closeErr
	}
	l.closed = true
	ctx, cancel := context.WithTimeout(context.Background(), admissionLookupTimeout)
	defer cancel()
	if err := l.store.Release(ctx, l.request.ID); err != nil || ctx.Err() != nil {
		l.closeErr = errAdmissionUnavailable
	}
	return l.closeErr
}
func minAdmissionTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}
