package auth

import (
	"context"
	"sync"
)

// cacheMutex is a zero-value-safe gate. Admission must be cancellable even
// while an unrelated invalidator owns the gate. Never copy after first use.
// Initialization does no application work and cannot wait on another lock.
type cacheMutex struct {
	once sync.Once
	held chan struct{}
}

func (m *cacheMutex) init() { m.once.Do(func() { m.held = make(chan struct{}, 1) }) }

func (m *cacheMutex) LockContext(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	m.init()
	select {
	case m.held <- struct{}{}:
	case <-ctx.Done():
		return ctx.Err()
	}
	// If both cases were ready, acquiring does not override cancellation.
	if err := ctx.Err(); err != nil {
		m.Unlock()
		return err
	}
	return nil
}

func (m *cacheMutex) Lock()   { m.init(); m.held <- struct{}{} }
func (m *cacheMutex) Unlock() { <-m.held }
