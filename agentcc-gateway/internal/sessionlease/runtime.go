package sessionlease

import (
	"context"
	"sync"
	"time"
)

// Guard bounds one authenticated runtime's lifetime. It does not replace the
// transport's INITIAL/RELEASE/CONTINUATION sink, revision or last-byte checks.
// Authorize must do fresh authority work and honor its context; never cache ALLOW.
type Guard struct {
	ctx       context.Context
	cancel    context.CancelCauseFunc
	store     Leases
	binding   Binding
	authorize func(context.Context) error
	mu        sync.Mutex
	expiry    *time.Timer
	deadline  time.Time
	absolute  time.Time
	idle      time.Time
	done      chan struct{}
	once      sync.Once
	closeErr  error
	// Immutable process-local ceilings derived once, before acquisition I/O.
	localIdle, localAbsolute time.Time
}

// Round(0) strips the monotonic reading. Equality here means it was absent:
// never silently fall back to wall time for runtime lifetime enforcement.
func remaining(deadline time.Time) (time.Duration, error) {
	now := time.Now()
	if now == now.Round(0) || deadline == deadline.Round(0) {
		return 0, ErrClock
	}
	delta := deadline.Sub(now)
	if delta <= 0 {
		return 0, ErrGone
	}
	return delta, nil
}
func earlier(deadline, idle, absolute time.Time) time.Time {
	if idle.Before(deadline) {
		deadline = idle
	}
	if absolute.Before(deadline) {
		deadline = absolute
	}
	return deadline
}
func localDeadline(start time.Time, l Lease, b Binding) (time.Time, error) {
	if start == start.Round(0) || l.ServerTime.IsZero() {
		return time.Time{}, ErrClock
	}
	if l.Binding != b || !l.ExpiresAt.After(l.ServerTime) || l.ExpiresAt.After(l.IdleDeadline) || l.IdleDeadline.After(l.AbsoluteDeadline) {
		return time.Time{}, ErrBinding
	}
	delta := l.ExpiresAt.Sub(l.ServerTime)
	if delta > 3*time.Second {
		return time.Time{}, ErrCorrupt
	}
	deadline := start.Add(delta)
	if _, err := remaining(deadline); err != nil {
		return time.Time{}, err
	}
	return deadline, nil
}
func Start(parent context.Context, store Leases, req Request, authorize func(context.Context) error) (*Guard, error) {
	if store == nil || authorize == nil {
		return nil, ErrUnavailable
	}
	authCtx, stop := context.WithTimeout(parent, operationBudget)
	err := authorize(authCtx)
	authErr := authCtx.Err()
	stop()
	if err != nil || authErr != nil {
		return nil, ErrUnavailable
	}
	start := time.Now()
	lease, err := store.Acquire(parent, req)
	if err != nil {
		cleanup, cancel := context.WithTimeout(context.Background(), operationBudget)
		_ = store.Release(cleanup, req.Binding)
		cancel()
		return nil, err
	}
	deadline, err := localDeadline(start, lease, req.Binding)
	if req.Idle < time.Millisecond || req.Idle > req.Absolute || req.Absolute > 24*time.Hour {
		err = ErrBinding
	}
	// The initial server remaining lifetimes may already be shorter (e.g. an
	// idempotent acquire). Request spans are additional caps, never extensions.
	localIdle := start.Add(min(lease.IdleDeadline.Sub(lease.ServerTime), req.Idle))
	localAbsolute := start.Add(min(lease.AbsoluteDeadline.Sub(lease.ServerTime), req.Absolute))
	deadline = earlier(deadline, localIdle, localAbsolute)
	wait, clockErr := remaining(deadline)
	if err != nil || clockErr != nil || parent.Err() != nil {
		cleanup, cancel := context.WithTimeout(context.Background(), operationBudget)
		_ = store.Release(cleanup, req.Binding)
		cancel()
		return nil, ErrUnavailable
	}
	ctx, cancel := context.WithCancelCause(parent)
	g := &Guard{ctx: ctx, cancel: cancel, store: store, binding: req.Binding, authorize: authorize, deadline: deadline, absolute: lease.AbsoluteDeadline, idle: lease.IdleDeadline, localIdle: localIdle, localAbsolute: localAbsolute, done: make(chan struct{})}
	g.expiry = time.AfterFunc(wait, func() { g.cancel(ErrGone) })
	go g.maintain()
	return g, nil
}
func (g *Guard) Context() context.Context { return g.ctx }
func (g *Guard) maintain() {
	defer close(g.done)
	for {
		g.mu.Lock()
		left, clockErr := remaining(g.deadline)
		wait := left / 3
		g.mu.Unlock()
		if clockErr != nil {
			g.cancel(clockErr)
			return
		}
		if wait <= 0 {
			g.cancel(ErrGone)
			return
		}
		timer := time.NewTimer(wait)
		select {
		case <-g.ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		authCtx, stop := context.WithTimeout(g.ctx, operationBudget)
		err := g.authorize(authCtx)
		authErr := authCtx.Err()
		stop()
		if err != nil || authErr != nil {
			g.cancel(ErrUnavailable)
			return
		}
		start := time.Now()
		lease, err := g.store.Renew(g.ctx, g.binding, false)
		if err != nil {
			g.cancel(err)
			return
		}
		if !lease.AbsoluteDeadline.Equal(g.absolute) || !lease.IdleDeadline.Equal(g.idle) {
			g.cancel(ErrBinding)
			return
		}
		deadline, err := localDeadline(start, lease, g.binding)
		if err != nil {
			g.cancel(err)
			return
		}
		// Never re-anchor hard lifetimes to renewal time or to Redis TIME. A
		// plateau can refresh TTL only inside the original local hard ceilings.
		deadline = earlier(deadline, g.localIdle, g.localAbsolute)
		g.mu.Lock()
		_, oldErr := remaining(g.deadline)
		nextWait, nextErr := remaining(deadline)
		if oldErr != nil || nextErr != nil {
			g.mu.Unlock()
			if oldErr != nil {
				g.cancel(oldErr)
			} else {
				g.cancel(nextErr)
			}
			return
		}
		// A completed expiry callback is sticky; a late reply cannot revive it.
		if g.ctx.Err() != nil || !g.expiry.Stop() {
			g.mu.Unlock()
			g.cancel(ErrGone)
			return
		}
		g.deadline = deadline
		g.expiry.Reset(nextWait)
		g.mu.Unlock()
	}
}

// Close fences locally before joining renewal, then releases using the original
// expected binding. Cleanup has a separate bounded context, never a new grant.
func (g *Guard) Close() error {
	g.once.Do(func() {
		g.cancel(ErrGone)
		<-g.done
		g.mu.Lock()
		g.expiry.Stop()
		g.mu.Unlock()
		ctx, cancel := context.WithTimeout(context.Background(), operationBudget)
		defer cancel()
		g.closeErr = g.store.Release(ctx, g.binding)
	})
	return g.closeErr
}
