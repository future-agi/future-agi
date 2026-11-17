package routebinding

import (
	"errors"
	"runtime"
	"sync"
	"testing"
	"time"
)

func TestScopePanicDrainsOutstandingUse(t *testing.T) {
	for _, write := range []bool{false, true} {
		t.Run(map[bool]string{false: "read", true: "write"}[write], func(t *testing.T) {
			g := testGate(t)
			entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
			start, unblock := scopeWorkers(t, release)
			scopes := make(chan *scope, 1)
			child := make(chan error, 1)
			panics := make(chan bool, 1)
			run := func(s *scope, use func(func() error) error) error {
				scopes <- s
				start(func() { child <- use(func() error { close(entered); <-release; close(finished); return nil }) })
				select {
				case <-entered:
				case <-release:
				}
				panic("fixed scope panic")
			}
			start(func() {
				defer func() { panics <- recover() != nil }()
				if write {
					_ = g.Publish(func(w *Write) error { return run(w.scope, func(fn func() error) error { return w.Use(g, fn) }) })
				} else {
					_ = g.Read(func(r *Read) error { return run(r.scope, func(fn func() error) error { return r.Use(g, fn) }) })
				}
			})
			var s *scope
			select {
			case s = <-scopes:
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: panic scope not reached")
			}
			reached(t, s.done)
			if g.mu.TryLock() {
				g.mu.Unlock()
				t.Error("panic released fence before drain")
			}
			if !errors.Is(s.use(func() error { return nil }), ErrScope) {
				t.Error("panic left scope admission open")
			}
			unblock()
			result(t, child)
			reached(t, finished)
			select {
			case propagated := <-panics:
				if !propagated {
					t.Fatal("panic not propagated")
				}
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: panic worker did not finish")
			}
			resultCh := make(chan error, 1)
			start(func() { resultCh <- g.Close() })
			result(t, resultCh)
		})
	}
}

// scopeWorkers releases the controlled use even on assertion failure, and joins
// all launched work. The watchdog reports failure only; it never proves order.
func scopeWorkers(t *testing.T, release chan struct{}) (func(func()), func()) {
	t.Helper()
	var wg sync.WaitGroup
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	t.Cleanup(func() {
		unblock()
		joined := make(chan struct{})
		go func() { wg.Wait(); close(joined) }()
		reached(t, joined)
	})
	return func(fn func()) {
		wg.Add(1)
		go func() { defer wg.Done(); fn() }()
	}, unblock
}

func TestConcurrentCloseReturnsAfterScopeDrain(t *testing.T) {
	g := testGate(t)
	entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	start, unblock := scopeWorkers(t, release)
	scopes := make(chan *scope, 1)
	readDone, child := make(chan error, 1), make(chan error, 1)
	start(func() {
		readDone <- g.Read(func(r *Read) error {
			scopes <- r.scope
			start(func() { child <- r.Use(g, func() error { close(entered); <-release; close(finished); return nil }) })
			select {
			case <-entered:
			case <-release:
			}
			return nil
		})
	})
	var s *scope
	select {
	case s = <-scopes:
	case <-time.After(5 * time.Second):
		t.Fatal("watchdog: read scope not reached")
	}
	reached(t, s.done)
	results := make(chan error, 3)
	for i := 0; i < 3; i++ {
		start(func() {
			err := g.Close()
			select {
			case <-finished:
			default:
				err = errors.New("close returned before use drain")
			}
			results <- err
		})
	}
	if g.mu.TryLock() {
		g.mu.Unlock()
		t.Error("fence absent while draining")
	}
	watchdog := time.NewTimer(5 * time.Second)
	defer watchdog.Stop()
	for g.mu.TryRLock() {
		g.mu.RUnlock()
		select {
		case <-watchdog.C:
			t.Fatal("watchdog: pending Close writer not witnessed")
		default:
			runtime.Gosched()
		}
	}
	// Only Close writers exist here, while the admitted read still holds its
	// fence: aggregate pendingClose witnessed; per-waiter queue registration UNPROVEN.
	unblock()
	result(t, child)
	result(t, readDone)
	for i := 0; i < 3; i++ {
		result(t, results)
	}
	if !errors.Is(g.Read(func(*Read) error { return nil }), ErrClosed) ||
		!errors.Is(g.Publish(func(*Write) error { return nil }), ErrClosed) {
		t.Fatal("close did not remain final")
	}
}

func TestPostClosureUseRefusesWhileDraining(t *testing.T) {
	for _, write := range []bool{false, true} {
		t.Run(map[bool]string{false: "read", true: "write"}[write], func(t *testing.T) {
			g := testGate(t)
			entered, release := make(chan struct{}), make(chan struct{})
			start, unblock := scopeWorkers(t, release)
			type token struct {
				s    *scope
				use  func(func() error) error
				next func() (Stamp, error)
			}
			tokens := make(chan token, 1)
			outer, child, refused := make(chan error, 1), make(chan error, 1), make(chan error, 1)
			run := func(tok token) error {
				tokens <- tok
				start(func() { child <- tok.use(func() error { close(entered); <-release; return nil }) })
				select {
				case <-entered:
				case <-release:
				}
				return nil
			}
			start(func() {
				if write {
					outer <- g.Publish(func(w *Write) error {
						return run(token{w.scope, func(fn func() error) error { return w.Use(g, fn) }, w.Next})
					})
				} else {
					outer <- g.Read(func(r *Read) error {
						return run(token{r.scope, func(fn func() error) error { return r.Use(g, fn) }, nil})
					})
				}
			})
			var tok token
			select {
			case tok = <-tokens:
			case <-time.After(5 * time.Second):
				t.Fatal("watchdog: token not reached")
			}
			reached(t, tok.s.done)
			if g.mu.TryLock() {
				g.mu.Unlock()
				t.Error("scope drain released its fence")
			}
			start(func() {
				called := false
				err := tok.use(func() error { called = true; return nil })
				if called || !errors.Is(err, ErrScope) {
					refused <- errors.New("post-closure Use accepted")
					return
				}
				if tok.next != nil {
					if stamp, err := tok.next(); stamp.Valid() || !errors.Is(err, ErrScope) {
						refused <- errors.New("post-closure Next accepted")
						return
					}
				}
				refused <- nil
			})
			result(t, refused) // Must finish before the admitted child is released.
			unblock()
			result(t, child)
			result(t, outer)
		})
	}
}
