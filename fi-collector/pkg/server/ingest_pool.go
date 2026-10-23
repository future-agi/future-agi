package server

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"time"

	"golang.org/x/sync/semaphore"
)

// memPool is a share of the ingest budget (see ingestLimits): bytes that
// requests lease before they hold them, and give back when they no longer do.
//
// There are two pools, always taken in the same order (wire, then work), so
// leases cannot deadlock: no request waits on a pool while holding a lease
// from a pool later in the order. Each is taken once per request, except
// that a gzip body that decompresses to more than its wire lease allows for
// waits for the rest while it holds its compressed body (readOTLPHTTPBody).
//
//   - wire: the request body, leased before it is read, and held until the
//     handler returns. HTTP: the Content-Length, or the cap when it is not
//     known (twice the cap for a plain body, which is joined after it is
//     read); for gzip, also room for the decompressed body. gRPC: about
//     twice the message, from its length prefix (grpc_export.go). It shrinks
//     once the real size is known.
//   - work: the price of decoding, converting and storing the request
//     (otlp_price.go), leased once, after the body is read and scanned. Its
//     handler part is released when the handler returns; its rows part goes
//     with the rows to the pending batch and is released when the drain that
//     stores them ends.
//
// The flusher, which is what returns the rows part, never waits on either
// pool. Waiters are served in arrival order (golang.org/x/sync/semaphore is
// FIFO), so a large request is not starved by a stream of small ones. A
// waiter keeps its place for as long as the pool keeps giving capacity back
// (some lease was released within the last wait), up to maxWait or the
// request's own deadline; then it is answered as busy, which OTLP exporters
// retry.
type memPool struct {
	name    string
	sem     *semaphore.Weighted
	size    int64
	wait    time.Duration // longest wait with nothing given back to the pool
	maxWait time.Duration // longest wait in all
	// onBlock, when set, runs whenever an acquire has to wait, and again
	// every wait while it does. The work pool uses it to start a drain,
	// which is what frees most of it.
	onBlock func()

	inUse     atomic.Int64 // bytes leased right now
	peak      atomic.Int64 // most bytes ever leased at once
	freed     atomic.Int64 // bytes ever given back: the pool's progress
	waiting   atomic.Int64 // acquires waiting right now
	waits     atomic.Int64 // acquires that had to wait
	waitNanos atomic.Int64 // time spent waiting, in total
	timeouts  atomic.Int64 // acquires that gave up
}

// leaseWait is how long a request waits for its share of a pool while
// nothing is given back to it; leaseMaxWait is how long it waits in all.
const (
	leaseWait    = 2 * time.Second
	leaseMaxWait = 10 * time.Second
)

func newMemPool(name string, size int64) *memPool {
	return &memPool{name: name, sem: semaphore.NewWeighted(size), size: size, wait: leaseWait, maxWait: leaseMaxWait}
}

var (
	// errPoolBusy: the pool did not have room in time. Retryable.
	errPoolBusy = errors.New("ingest budget busy")
	// errLeaseTooLarge: the request needs more than the whole pool. Permanent.
	errLeaseTooLarge = errors.New("request larger than the ingest budget")
)

// acquire leases n bytes, waiting behind earlier waiters for as long as the
// pool makes progress (see memPool).
func (p *memPool) acquire(ctx context.Context, n int64) (*lease, error) {
	if n < 0 || n > p.size {
		return nil, errLeaseTooLarge
	}
	if err := p.take(ctx, n); err != nil {
		return nil, err
	}
	return &lease{p: p, held: n}, nil
}

// take takes n bytes from the semaphore and accounts for them.
func (p *memPool) take(ctx context.Context, n int64) error {
	if !p.sem.TryAcquire(n) {
		if err := p.waitFor(ctx, n); err != nil {
			return err
		}
	}
	now := p.inUse.Add(n)
	for pk := p.peak.Load(); now > pk && !p.peak.CompareAndSwap(pk, now); pk = p.peak.Load() {
	}
	return nil
}

// waitFor waits in line for n bytes. It gives up (errPoolBusy) when nothing
// was given back to the pool for p.wait, after p.maxWait, or when ctx ends.
func (p *memPool) waitFor(ctx context.Context, n int64) error {
	if p.onBlock != nil {
		p.onBlock()
	}
	p.waits.Add(1)
	p.waiting.Add(1)
	start := time.Now()
	wctx, cancel := context.WithTimeout(ctx, p.maxWait)
	stop := make(chan struct{})
	go func() {
		t := time.NewTicker(p.wait)
		defer t.Stop()
		last := p.freed.Load()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
			}
			now := p.freed.Load()
			if now == last {
				cancel()
				return
			}
			last = now
			if p.onBlock != nil {
				p.onBlock()
			}
		}
	}()
	err := p.sem.Acquire(wctx, n)
	close(stop)
	cancel()
	p.waiting.Add(-1)
	p.waitNanos.Add(int64(time.Since(start)))
	if err != nil {
		p.timeouts.Add(1)
		return errPoolBusy
	}
	return nil
}

// lease is a request's share of a pool. A nil lease is valid and holds
// nothing.
type lease struct {
	p    *memPool
	mu   sync.Mutex
	held int64
	done bool
}

// size is what the lease holds now.
func (l *lease) size() int64 {
	if l == nil {
		return 0
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.held
}

// shrink gives back what the lease holds beyond n. It never grows a lease.
func (l *lease) shrink(n int64) {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.done || n >= l.held {
		return
	}
	n = max(n, 0)
	l.give(l.held - n)
	l.held = n
}

// grow adds n bytes to the lease, waiting for them as acquire does. It is
// for a gzip body that decompresses to more than its wire lease allowed for
// (readOTLPHTTPBody); nothing else holds a lease while it waits on the same
// pool.
func (l *lease) grow(ctx context.Context, n int64) error {
	if n <= 0 {
		return nil
	}
	if l.size()+n > l.p.size {
		return errLeaseTooLarge
	}
	if err := l.p.take(ctx, n); err != nil {
		return err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.done { // released while it waited
		l.give(n)
		return errPoolBusy
	}
	l.held += n
	return nil
}

// split moves n bytes of the lease into a new lease of the same pool.
func (l *lease) split(n int64) *lease {
	l.mu.Lock()
	defer l.mu.Unlock()
	n = min(max(n, 0), l.held)
	l.held -= n
	return &lease{p: l.p, held: n}
}

// release gives the lease back. It may be called more than once.
func (l *lease) release() {
	if l == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.done {
		return
	}
	l.done = true
	l.give(l.held)
	l.held = 0
}

func (l *lease) give(n int64) {
	if n == 0 {
		return
	}
	l.p.inUse.Add(-n)
	l.p.freed.Add(n)
	l.p.sem.Release(n)
}

func noRelease() {}

// logSample lets one log line through per second and counts the others.
type logSample struct {
	next       atomic.Int64 // unix nanoseconds before which lines are held back
	suppressed atomic.Int64
}

// allow reports whether to log now, and how many lines were held back since
// the last one that was logged.
func (l *logSample) allow(now time.Time) (bool, int64) {
	t := now.UnixNano()
	next := l.next.Load()
	if t < next || !l.next.CompareAndSwap(next, t+int64(time.Second)) {
		l.suppressed.Add(1)
		return false, 0
	}
	return true, l.suppressed.Swap(0)
}
