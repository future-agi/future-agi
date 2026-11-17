package sessionlease

import (
	"context"
	_ "embed"
)

//go:embed scripts/directory.lua
var directoryScript string

//go:embed scripts/lease.lua
var leaseScript string

type Store struct {
	policy Policy
	eval   func(context.Context, string, []string, ...any) (any, error)
	close  func() error
}

var _ Leases = (*Store)(nil)

func (s *Store) execute(ctx context.Context, script string, keys []string, args ...any) (any, error) {
	if s == nil || s.eval == nil || ctx.Err() != nil {
		return nil, ErrUnavailable
	}
	raw, err := s.eval(ctx, script, keys, args...)
	if err != nil || ctx.Err() != nil {
		return nil, ErrUnavailable
	}
	return raw, nil
}
func (s *Store) directory(ctx context.Context, op string, r routing) (string, error) {
	raw, err := s.execute(ctx, directoryScript, []string{r.directoryKey()}, op, r.directory, r.id)
	if err != nil {
		return "", err
	}
	value, ok := raw.(string)
	if !ok {
		return "", ErrCorrupt
	}
	switch value {
	case "GONE":
		return "", ErrGone
	case "BINDING":
		return "", ErrBinding
	case "CORRUPT":
		return "", ErrCorrupt
	}
	return value, nil
}
func (s *Store) Acquire(parent context.Context, req Request) (Lease, error) {
	if s == nil {
		return Lease{}, ErrUnavailable
	}
	if err := validateRequest(req, s.policy); err != nil {
		return Lease{}, err
	}
	ctx, cancel := context.WithTimeout(parent, operationBudget)
	defer cancel()
	r := routeFor(req, s.policy)
	directory, err := s.directory(ctx, "acquire", r)
	if err != nil {
		return Lease{}, err
	}
	if directory != r.directory {
		return Lease{}, ErrBinding
	}
	raw, err := s.execute(ctx, leaseScript, r.keys, "acquire", directory, r.id, policyDigest(s.policy), req.TTL.Milliseconds(), req.Idle.Milliseconds(), req.Absolute.Milliseconds(), req.Limits.Org, req.Limits.Workspace, req.Limits.Session, 0)
	if err != nil {
		return Lease{}, err
	}
	return decodeLease(raw, req.Binding)
}
func (s *Store) Renew(parent context.Context, b Binding, activity bool) (Lease, error) {
	if s == nil {
		return Lease{}, ErrUnavailable
	}
	if err := validateBinding(b); err != nil {
		return Lease{}, err
	}
	ctx, cancel := context.WithTimeout(parent, operationBudget)
	defer cancel()
	rawDirectory, err := s.directory(ctx, "read", routeBinding(b))
	if err != nil {
		return Lease{}, err
	}
	r, err := bindDirectory(rawDirectory, b, s.policy)
	if err != nil {
		return Lease{}, err
	}
	active := 0
	if activity {
		active = 1
	}
	l := s.policy.limits(b.Purpose)
	raw, err := s.execute(ctx, leaseScript, r.keys, "renew", r.directory, r.id, policyDigest(s.policy), 0, 0, 0, l.Org, l.Workspace, l.Session, active)
	if err != nil {
		return Lease{}, err
	}
	return decodeLease(raw, b)
}
func (s *Store) Release(parent context.Context, b Binding) error {
	if s == nil {
		return ErrUnavailable
	}
	if err := validateBinding(b); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(parent, operationBudget)
	defer cancel()
	rawDirectory, err := s.directory(ctx, "release", routeBinding(b))
	if err != nil {
		return err
	}
	if rawDirectory == "*" {
		return nil
	}
	r, err := bindDirectory(rawDirectory, b, s.policy)
	if err != nil {
		return err
	}
	l := s.policy.limits(b.Purpose)
	raw, err := s.execute(ctx, leaseScript, r.keys, "release", r.directory, r.id, policyDigest(s.policy), 0, 0, 0, l.Org, l.Workspace, l.Session, 0)
	if err != nil {
		return err
	}
	value, ok := raw.([]any)
	if !ok || len(value) != 1 {
		return ErrCorrupt
	}
	if value[0] == "OK" {
		return nil
	}
	return statusError(value[0])
}
