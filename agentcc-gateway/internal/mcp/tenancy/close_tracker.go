package tenancy

import (
	"context"
	"sync"
)

// closeTracker counts client closes in flight. Unlike a WaitGroup it may be
// added to while someone waits: a background connect can retire its entry
// after Close began.
type closeTracker struct {
	mu   sync.Mutex
	n    int
	idle chan struct{} // closed when n drops to 0; nil while n is 0
}

func (t *closeTracker) add() {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.n == 0 {
		t.idle = make(chan struct{})
	}
	t.n++
}

func (t *closeTracker) done() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.n--
	if t.n == 0 {
		close(t.idle)
		t.idle = nil
	}
}

// wait returns once no close is in flight, or with ctx's error.
func (t *closeTracker) wait(ctx context.Context) error {
	for {
		t.mu.Lock()
		idle := t.idle
		t.mu.Unlock()
		if idle == nil {
			return nil
		}
		select {
		case <-idle:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}
