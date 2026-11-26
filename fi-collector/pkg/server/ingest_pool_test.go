package server

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

// Tests for memPool's waits (ingest_pool.go).

// holdLeases leases n leases of size each from p.
func holdLeases(t *testing.T, p *memPool, n int, size int64) []*lease {
	t.Helper()
	var ls []*lease
	for i := 0; i < n; i++ {
		l, err := p.acquire(context.Background(), size)
		if err != nil {
			t.Fatalf("lease %d: %v", i, err)
		}
		ls = append(ls, l)
	}
	return ls
}

// A waiter keeps its place for as long as the pool keeps giving capacity
// back, even when what it needs is free only after more than the wait: the
// pool is busy, not stuck. (Before, every waiter gave up after a fixed 2 s,
// so a request needing most of the work pool was refused whenever the drains
// that free it took longer than that.)
func TestPoolWaiterKeepsItsPlaceWhileThePoolMakesProgress(t *testing.T) {
	p := newMemPool("work", 100)
	p.wait = 300 * time.Millisecond
	held := holdLeases(t, p, 10, 10)
	go func() {
		for _, l := range held { // one back every 100 ms: 70 free after 700 ms
			time.Sleep(100 * time.Millisecond)
			l.release()
		}
	}()
	start := time.Now()
	l, err := p.acquire(context.Background(), 70)
	if err != nil {
		t.Fatalf("acquire while the pool gives capacity back: %v after %v, want the lease", err, time.Since(start))
	}
	if waited := time.Since(start); waited < 2*p.wait {
		t.Errorf("got the lease after %v; the test needs a wait longer than the %v without progress", waited, p.wait)
	}
	l.release()
}

// A waiter gives up when nothing is given back to the pool for the wait,
// and after the longest wait even while the pool makes progress.
func TestPoolWaiterGivesUp(t *testing.T) {
	t.Run("no progress", func(t *testing.T) {
		p := newMemPool("work", 100)
		p.wait = 200 * time.Millisecond
		held := holdLeases(t, p, 1, 100)
		start := time.Now()
		if _, err := p.acquire(context.Background(), 10); !errors.Is(err, errPoolBusy) {
			t.Fatalf("acquire: %v, want errPoolBusy", err)
		}
		if waited := time.Since(start); waited > 3*p.wait {
			t.Errorf("gave up after %v, want about %v", waited, p.wait)
		}
		held[0].release()
		if p.timeouts.Load() != 1 || p.waiting.Load() != 0 {
			t.Errorf("timeouts %d waiting %d, want 1 and 0", p.timeouts.Load(), p.waiting.Load())
		}
	})
	t.Run("longest wait", func(t *testing.T) {
		p := newMemPool("work", 100)
		p.wait, p.maxWait = 100*time.Millisecond, 500*time.Millisecond
		big := holdLeases(t, p, 1, 90)[0]
		stop := make(chan struct{})
		defer close(stop)
		go func() { // progress, a byte every 20 ms: 50 free only after 800 ms
			for n := int64(89); n > 0; n-- {
				select {
				case <-stop:
					return
				case <-time.After(20 * time.Millisecond):
				}
				big.shrink(n)
			}
		}()
		start := time.Now()
		if _, err := p.acquire(context.Background(), 50); !errors.Is(err, errPoolBusy) {
			t.Fatalf("acquire: %v, want errPoolBusy", err)
		}
		if waited := time.Since(start); waited < p.maxWait || waited > 2*p.maxWait {
			t.Errorf("gave up after %v, want about the %v longest wait", waited, p.maxWait)
		}
	})
	t.Run("request deadline", func(t *testing.T) {
		p := newMemPool("work", 100)
		holdLeases(t, p, 1, 100)
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
		defer cancel()
		start := time.Now()
		if _, err := p.acquire(ctx, 10); !errors.Is(err, errPoolBusy) {
			t.Fatalf("acquire: %v, want errPoolBusy", err)
		}
		if waited := time.Since(start); waited > time.Second {
			t.Errorf("gave up after %v, want at the request's deadline", waited)
		}
	})
}

// A lease grows by waiting in line like any other acquire, and cannot grow
// past the pool.
func TestLeaseGrow(t *testing.T) {
	p := newMemPool("wire", 100)
	p.wait = 200 * time.Millisecond
	l, _ := p.acquire(context.Background(), 40)
	other, _ := p.acquire(context.Background(), 50)
	time.AfterFunc(100*time.Millisecond, other.release)
	if err := l.grow(context.Background(), 30); err != nil {
		t.Fatalf("grow: %v", err)
	}
	if l.size() != 70 || p.inUse.Load() != 70 {
		t.Errorf("lease %d, pool in use %d; want 70 and 70", l.size(), p.inUse.Load())
	}
	if err := l.grow(context.Background(), 31); !errors.Is(err, errLeaseTooLarge) {
		t.Errorf("grow past the pool: %v, want errLeaseTooLarge", err)
	}
	l.release()
	if p.inUse.Load() != 0 {
		t.Errorf("%d bytes in use after release", p.inUse.Load())
	}
}

// A waiter keeps its place while the pool is busy freeing capacity (for the
// work pool, a drain storing rows), even when nothing was given back for
// longer than the wait. (Before, a waiter gave up whenever one drain took
// longer than the wait, as large drains to a slow ClickHouse do.)
func TestPoolWaiterWaitsWhileThePoolIsBusy(t *testing.T) {
	p := newMemPool("work", 100)
	p.wait = 200 * time.Millisecond
	var draining atomic.Bool
	draining.Store(true)
	p.busy = draining.Load
	held := holdLeases(t, p, 1, 100)[0]
	time.AfterFunc(700*time.Millisecond, func() { draining.Store(false); held.release() }) // one drain, 700 ms
	start := time.Now()
	l, err := p.acquire(context.Background(), 50)
	if err != nil {
		t.Fatalf("acquire during a drain: %v after %v, want the lease when the drain ends", err, time.Since(start))
	}
	l.release()
}

// Concurrent wire growers retain their initial shares, but do not wait forever.
// This models gzip inflation and joining unknown-length plain bodies alike.
func TestWireGrowersStopAtNoProgressTimeout(t *testing.T) {
	p := newMemPool("wire", 100)
	ls := holdLeases(t, p, 2, 50)
	defer ls[0].release()
	defer ls[1].release()
	done := make(chan error, 2)
	start := time.Now()
	for _, l := range ls {
		go func(l *lease) { done <- l.grow(context.Background(), 1) }(l)
	}
	for range ls {
		if err := <-done; !errors.Is(err, errPoolBusy) {
			t.Fatalf("grow=%v", err)
		}
	}
	if elapsed := time.Since(start); elapsed < leaseWait || elapsed > leaseWait+time.Second {
		t.Fatalf("waited %v", elapsed)
	}
	if p.inUse.Load() != 100 {
		t.Fatal("grow changed held leases after refusal")
	}
}
