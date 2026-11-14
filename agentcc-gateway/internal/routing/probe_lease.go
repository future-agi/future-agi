package routing

import (
	"context"
	"errors"
	"sync"
)

// ProbeLease owns one admission on the original breaker and recovery generation.
// Completion is exactly once even when terminal and abandonment paths race.
// A nil lease is a valid, disabled-breaker admission.
type ProbeLease struct {
	once       sync.Once
	breaker    *CircuitBreaker
	generation uint64
}

func (cb *CircuitBreaker) Acquire() (*ProbeLease, bool) {
	if cb == nil {
		return nil, true
	}
	generation, allowed := cb.Allow()
	if !allowed {
		return nil, false
	}
	return &ProbeLease{breaker: cb, generation: generation}, true
}

// Abandon releases capacity without recording recovery or changing failure time.
func (l *ProbeLease) Abandon() {
	if l != nil {
		l.once.Do(func() { l.breaker.RecordFailure(l.generation, nil) })
	}
}

// Complete classifies the raw upstream outcome before cleanup cancellation or
// conversion to a client-facing error. The context belongs to the call lifetime.
func (l *ProbeLease) Complete(ctx context.Context, err error) {
	if l == nil {
		return
	}
	if errors.Is(ctx.Err(), context.Canceled) || (ctx.Err() == nil && errors.Is(err, context.Canceled)) {
		l.Abandon()
		return
	}
	if ctx.Err() != nil {
		err = ctx.Err()
	}
	l.once.Do(func() {
		if err == nil {
			l.breaker.RecordSuccess(l.generation)
		} else {
			l.breaker.RecordFailure(l.generation, err)
		}
	})
}
