package sessionlease

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

type fakeLeases struct {
	released atomic.Int32
	renew    func(context.Context, Binding, bool) (Lease, error)
}

func (s *fakeLeases) Acquire(_ context.Context, r Request) (Lease, error) {
	n := time.Unix(1, 0)
	return Lease{Binding: r.Binding, ServerTime: n, ExpiresAt: n.Add(90 * time.Millisecond), IdleDeadline: n.Add(time.Second), AbsoluteDeadline: n.Add(time.Second)}, nil
}
func (s *fakeLeases) Renew(c context.Context, b Binding, a bool) (Lease, error) {
	return s.renew(c, b, a)
}
func (s *fakeLeases) Release(context.Context, Binding) error { s.released.Add(1); return nil }
func TestRuntimeCancelsOnRenewalFailureAndReleasesOnce(t *testing.T) {
	s := &fakeLeases{renew: func(context.Context, Binding, bool) (Lease, error) { return Lease{}, ErrUnavailable }}
	g, err := Start(context.Background(), s, fixtureRequest("synthetic-id"), func(context.Context) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-g.Context().Done():
	case <-time.After(time.Second):
		t.Fatal("runtime did not cancel")
	}
	_ = g.Close()
	_ = g.Close()
	if s.released.Load() != 1 {
		t.Fatal("release must be idempotent")
	}
}
func TestRuntimeExpirationCancelsWhileAuthorityIsBlocked(t *testing.T) {
	var calls atomic.Int32
	s := &fakeLeases{renew: func(context.Context, Binding, bool) (Lease, error) {
		t.Error("renew after failed authority")
		return Lease{}, ErrUnavailable
	}}
	g, err := Start(context.Background(), s, fixtureRequest("synthetic-id"), func(ctx context.Context) error {
		if calls.Add(1) > 1 {
			<-ctx.Done()
			return ctx.Err()
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-g.Context().Done():
	case <-time.After(time.Second):
		t.Fatal("blocked authority extended lease")
	}
	_ = g.Close()
}
func TestRuntimeRejectsBindingMismatch(t *testing.T) {
	n := time.Unix(1, 0)
	l := Lease{Binding: fixtureRequest("wrong-id").Binding, ServerTime: n, ExpiresAt: n.Add(time.Second), IdleDeadline: n.Add(time.Second), AbsoluteDeadline: n.Add(time.Second)}
	if _, err := localDeadline(time.Now(), l, fixtureRequest("synthetic-id").Binding); err == nil {
		t.Fatal("lease rebound")
	}
}

type ambiguousAcquire struct{ fakeLeases }

func (s *ambiguousAcquire) Acquire(context.Context, Request) (Lease, error) {
	return Lease{}, ErrUnavailable
}
func TestRuntimeFencesAmbiguousAcquire(t *testing.T) {
	s := &ambiguousAcquire{}
	if _, err := Start(context.Background(), s, fixtureRequest("synthetic-id"), func(context.Context) error { return nil }); err != ErrUnavailable {
		t.Fatal(err)
	}
	if s.released.Load() != 1 {
		t.Fatal("uncertain acquire was not fenced")
	}
}
func TestRuntimeRejectsExtendedAbsoluteCeiling(t *testing.T) {
	n := time.Unix(1, 0)
	s := &fakeLeases{renew: func(_ context.Context, b Binding, _ bool) (Lease, error) {
		return Lease{Binding: b, ServerTime: n, ExpiresAt: n.Add(90 * time.Millisecond), IdleDeadline: n.Add(time.Second), AbsoluteDeadline: n.Add(2 * time.Second)}, nil
	}}
	g, err := Start(context.Background(), s, fixtureRequest("synthetic-id"), func(context.Context) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	defer g.Close()
	select {
	case <-g.Context().Done():
	case <-time.After(time.Second):
		t.Fatal("absolute ceiling changed")
	}
}

// plateauLeases returns successful replies while Redis TIME never advances.
// Every renewal keeps the original server idle/absolute timestamps unchanged.
type plateauLeases struct {
	fakeLeases
	lease   Lease
	renewed atomic.Int32
}

func (s *plateauLeases) Acquire(_ context.Context, r Request) (Lease, error) {
	l := s.lease
	l.Binding = r.Binding
	return l, nil
}
func (s *plateauLeases) Renew(_ context.Context, b Binding, activity bool) (Lease, error) {
	s.renewed.Add(1)
	l := s.lease
	l.Binding = b
	return l, nil
}

func TestRuntimeStalledServerCannotExtendCreationCeilings(t *testing.T) {
	for _, tc := range []struct {
		name                                                         string
		serverIdle, serverAbsolute, requestedIdle, requestedAbsolute time.Duration
	}{
		{"server-idle", 900 * time.Millisecond, 2 * time.Second, 3 * time.Second, 3 * time.Second},
		{"server-absolute", 900 * time.Millisecond, 900 * time.Millisecond, 3 * time.Second, 3 * time.Second},
		{"requested-idle", 3 * time.Second, 3 * time.Second, 900 * time.Millisecond, 2 * time.Second},
		{"requested-absolute", 3 * time.Second, 3 * time.Second, 900 * time.Millisecond, 900 * time.Millisecond},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Real monotonic time is intentional: Go's synctest clock strips the
			// monotonic reading, which this guard must refuse, not qualify.
			n := time.Unix(1, 0)
			s := &plateauLeases{lease: Lease{ServerTime: n, ExpiresAt: n.Add(300 * time.Millisecond), IdleDeadline: n.Add(tc.serverIdle), AbsoluteDeadline: n.Add(tc.serverAbsolute)}}
			r := fixtureRequest("synthetic-stalled-time")
			r.TTL, r.Idle, r.Absolute = 300*time.Millisecond, tc.requestedIdle, tc.requestedAbsolute
			var allowed atomic.Int32
			g, err := Start(context.Background(), s, r, func(context.Context) error { allowed.Add(1); return nil })
			if err != nil {
				t.Fatal(err)
			}
			defer g.Close()
			latestHard := time.Now().Add(900 * time.Millisecond)
			// Prove healthy renewal extends the short TTL, not premature refusal.
			healthy := time.NewTimer(450 * time.Millisecond)
			defer healthy.Stop()
			select {
			case <-g.Context().Done():
				t.Fatal("healthy plateau renewal cancelled before the original hard ceiling")
			case <-healthy.C:
			}
			if s.renewed.Load() < 2 || allowed.Load() < 3 {
				t.Fatal("healthy plateau renewals were not exercised")
			}
			// Scheduling tolerance is observation slack, never a grant extension.
			limit := time.NewTimer(time.Until(latestHard.Add(250 * time.Millisecond)))
			defer limit.Stop()
			select {
			case <-g.Context().Done():
				if context.Cause(g.Context()) != ErrGone {
					t.Fatal("unexpected cancellation cause")
				}
				g.mu.Lock()
				deadline := g.deadline
				g.mu.Unlock()
				if deadline.After(latestHard) {
					t.Fatal("timer exceeded original hard ceiling")
				}
			case <-limit.C:
				t.Fatal("successful plateau renewals escaped the creation-time hard ceiling")
			}

		})
	}
}

func TestRuntimeRefusesUnestablishedTime(t *testing.T) {
	n := time.Unix(1, 0)
	b := fixtureRequest("synthetic-time").Binding
	valid := Lease{Binding: b, ServerTime: n, ExpiresAt: n.Add(time.Second), IdleDeadline: n.Add(time.Second), AbsoluteDeadline: n.Add(time.Second)}
	for _, start := range []time.Time{time.Time{}, time.Now().Round(0)} {
		if _, err := localDeadline(start, valid, b); err != ErrClock {
			t.Fatalf("missing monotonic source: got %v, want ErrClock", err)
		}
	}
	s := &plateauLeases{lease: Lease{ExpiresAt: time.Time{}.Add(90 * time.Millisecond), IdleDeadline: time.Time{}.Add(time.Second), AbsoluteDeadline: time.Time{}.Add(time.Second)}}
	g, err := Start(context.Background(), s, fixtureRequest("synthetic-missing-server-time"), func(context.Context) error { return nil })
	if g != nil {
		_ = g.Close()
	}
	if err != ErrUnavailable || g != nil || s.released.Load() != 1 {
		t.Fatal("missing server time did not refuse and fence acquisition")
	}
}
