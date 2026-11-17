package routebinding

import (
	"errors"
	"testing"
	"time"
)

func reached(t *testing.T, c <-chan struct{}) {
	t.Helper()
	select {
	case <-c:
	case <-time.After(5 * time.Second):
		t.Fatal("watchdog: expected reached state absent")
	}
}

func result(t *testing.T, c <-chan error) {
	t.Helper()
	select {
	case err := <-c:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("watchdog: operation did not drain")
	}
}

func TestReadScopeDrainsConcurrentUseBeforeFenceRelease(t *testing.T) {
	g := testGate(t)
	entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	callbackResult, childResult := make(chan error, 1), make(chan error, 1)
	tokens := make(chan *Read, 1)
	state := 0 // Protected by the actual gate fence, not a test-only mutex.
	go func() {
		callbackResult <- g.Read(func(r *Read) error {
			tokens <- r
			go func() {
				childResult <- r.Use(g, func() error {
					close(entered)
					<-release
					if state != 0 {
						return errors.New("writer crossed an active owner read")
					}
					close(finished)
					return nil
				})
			}()
			<-entered
			return nil // Scope teardown must drain the already admitted use.
		})
	}()
	var r *Read
	select {
	case r = <-tokens:
	case <-time.After(5 * time.Second):
		t.Fatal("watchdog: callback not reached")
	}
	reached(t, r.scope.done) // Teardown has closed admission, not just returned soon.
	if g.mu.TryLock() {
		g.mu.Unlock()
		t.Error("fence released while owner read was still executing")
	}
	if err := r.Use(g, func() error { return errors.New("post-scope read ran") }); !errors.Is(err, ErrScope) {
		t.Error("post-scope use not rejected while draining")
	}
	publishResult := make(chan error, 1)
	go func() {
		publishResult <- g.Publish(func(w *Write) error {
			return w.Use(g, func() error {
				select {
				case <-finished:
				default:
					return errors.New("publication preceded owner read completion")
				}
				state = 1
				return nil
			})
		})
	}()
	close(release)
	result(t, childResult)
	result(t, callbackResult)
	result(t, publishResult)
	if state != 1 {
		t.Fatal("publication absent")
	}
}

func TestWriteScopeDrainsConcurrentUseBeforeReadAndClose(t *testing.T) {
	g := testGate(t)
	entered, release := make(chan struct{}), make(chan struct{})
	callbackResult, childResult := make(chan error, 1), make(chan error, 1)
	tokens := make(chan *Write, 1)
	state := 0
	go func() {
		callbackResult <- g.Publish(func(w *Write) error {
			tokens <- w
			go func() {
				childResult <- w.Use(g, func() error {
					close(entered)
					<-release
					state = 1
					return nil
				})
			}()
			<-entered
			return nil
		})
	}()
	var w *Write
	select {
	case w = <-tokens:
	case <-time.After(5 * time.Second):
		t.Fatal("watchdog: callback not reached")
	}
	reached(t, w.scope.done)
	if g.mu.TryRLock() {
		g.mu.RUnlock()
		t.Error("write fence released before concurrent use drained")
	}
	if _, err := w.Next(); !errors.Is(err, ErrScope) {
		t.Error("post-callback Next accepted")
	}
	close(release)
	result(t, childResult)
	result(t, callbackResult)
	if err := g.Read(func(r *Read) error {
		return r.Use(g, func() error {
			if state != 1 {
				return errors.New("reader did not observe completed publication")
			}
			return nil
		})
	}); err != nil {
		t.Fatal(err)
	}
	if err := g.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestReadScopeSerializesConcurrentOwnerUses(t *testing.T) {
	g := testGate(t)
	if err := g.Read(func(r *Read) error {
		entered, release := make(chan struct{}), make(chan struct{})
		first, second := make(chan error, 1), make(chan error, 1)
		value := 0
		go func() {
			first <- r.Use(g, func() error {
				close(entered)
				<-release
				value++
				return nil
			})
		}()
		<-entered
		if r.scope.mu.TryLock() {
			r.scope.mu.Unlock()
			close(release)
			result(t, first)
			return errors.New("owner use did not own scope mutex")
		}
		go func() {
			second <- r.Use(g, func() error { value++; return nil })
		}()
		close(release)
		result(t, first)
		result(t, second)
		if value != 2 {
			return errors.New("concurrent owner uses did not serialize")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
