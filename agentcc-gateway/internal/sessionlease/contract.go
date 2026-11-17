// Package sessionlease implements shared, scope-bound admission leases.
// It is not membership authority. Only authenticated server code may call it.
package sessionlease

import (
	"context"
	"errors"
	"time"
)

type Purpose uint8

const (
	Session Purpose = iota + 1
	Call
	Stream
)

type Scope struct{ OrgID, WorkspaceID string }
type Actor struct{ Kind, PersonID, CredentialID string }
type Binding struct {
	ID          string
	Scope       Scope
	Actor       Actor
	SessionHash string
	Purpose     Purpose
}
type Limits struct{ Org, Workspace, Session int }
type Request struct {
	Binding
	Limits              Limits
	TTL, Idle, Absolute time.Duration
}
type Lease struct {
	Binding
	ServerTime, ExpiresAt, IdleDeadline, AbsoluteDeadline time.Time
}
type Policy struct {
	Sessions, Calls, Streams Limits
	MaxIdle, MaxAbsolute     time.Duration
	Version                  string
}

// Leases deliberately requires EXPECTED binding for every mutation. The old
// MCP ID-only interface must be adapted from authenticated stored records, not
// a context scalar or a request-selected workspace. Main owns that ABI join.
type Leases interface {
	Acquire(context.Context, Request) (Lease, error)
	Renew(context.Context, Binding, bool) (Lease, error)
	Release(context.Context, Binding) error
}

var (
	ErrCapacity    = errors.New("session lease capacity exceeded")
	ErrUnavailable = errors.New("session lease unavailable")
	ErrBinding     = errors.New("session lease binding mismatch")
	ErrGone        = errors.New("session lease gone")
	ErrCorrupt     = errors.New("session lease state invalid")
	ErrClock       = errors.New("session lease clock regressed")
)

const operationBudget = 500 * time.Millisecond

func bounded(s string) bool { return len(s) > 0 && len(s) <= 512 }
func validateBinding(b Binding) error {
	if !bounded(b.ID) || !bounded(b.Scope.OrgID) || !bounded(b.Scope.WorkspaceID) || !bounded(b.Actor.PersonID) || !bounded(b.Actor.CredentialID) || !bounded(b.SessionHash) {
		return ErrBinding
	}
	if b.Actor.Kind != "gateway_key" && b.Actor.Kind != "oauth" {
		return ErrBinding
	}
	if b.Purpose < Session || b.Purpose > Stream {
		return ErrBinding
	}
	return nil
}
func validLimits(l Limits) bool {
	return l.Org > 0 && l.Org <= 1000000 && l.Workspace > 0 && l.Workspace <= l.Org && l.Session >= 0 && l.Session <= l.Workspace
}
func validatePolicy(p Policy) error {
	if !bounded(p.Version) || !validLimits(p.Sessions) || !validLimits(p.Calls) || !validLimits(p.Streams) || p.MaxIdle < time.Millisecond || p.MaxIdle > p.MaxAbsolute || p.MaxAbsolute > 24*time.Hour {
		return ErrBinding
	}
	return nil
}
func (p Policy) limits(purpose Purpose) Limits {
	switch purpose {
	case Session:
		return p.Sessions
	case Call:
		return p.Calls
	case Stream:
		return p.Streams
	}
	return Limits{}
}
func validateRequest(r Request, p Policy) error {
	if validateBinding(r.Binding) != nil || validatePolicy(p) != nil || r.Limits != p.limits(r.Purpose) {
		return ErrBinding
	}
	if r.TTL < time.Millisecond || r.TTL > 3*time.Second || r.Idle < time.Millisecond || r.Idle > p.MaxIdle || r.Idle > r.Absolute || r.Absolute > p.MaxAbsolute {
		return ErrBinding
	}
	if r.Purpose == Call && r.Absolute > 30*time.Second {
		return ErrBinding
	}
	return nil
}
