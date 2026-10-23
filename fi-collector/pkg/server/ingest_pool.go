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
// There are two pools, always taken in the same order and each at most once
// per request, so no request ever waits while holding capacity it could be
// waiting for:
//
//   - wire: the request body, leased before it is read (Content-Length, or
//     the cap when the length is not known; for gzip, also the decompressed
//     cap; for gRPC, twice the receive cap, as grpc-go joins the frames of a
//     message into a copy). It only shrinks once the real size is known, and
//     is released when the handler returns.
//   - work: the price of decoding, converting and storing the request
//     (otlp_price.go), leased once, after the body is read and scanned. Its
//     handler part is released when the handler returns; its rows part goes
//     with the rows to the pending batch and is released when the drain that
//     stores them ends.
//
// The flusher, which is what returns the rows part, never waits on either
// pool. Waiters are served in arrival order (golang.org/x/sync/semaphore is
// FIFO), so a large request is not starved by a stream of small ones; a
// request that does not get its lease within wait is answered as busy, which
// OTLP exporters retry.
type memPool struct {
	name string
	sem  *semaphore.Weighted
	size int64
	wait time.Duration
	// onBlock, when set, runs whenever an acquire has to wait. The work pool
	// uses it to start a drain, which is what frees most of it.
	onBlock func()

	inUse     atomic.Int64 // bytes leased right now
	peak      atomic.Int64 // most bytes ever leased at once
	waits     atomic.Int64 // acquires that had to wait
	waitNanos atomic.Int64 // time spent waiting, in total
	timeouts  atomic.Int64 // acquires that gave up
}

// leaseWait is how long a request waits for its share of a pool before it is
// answered as busy.
const leaseWait = 2 * time.Second

func newMemPool(name string, size int64) *memPool {
	return &memPool{name: name, sem: semaphore.NewWeighted(size), size: size, wait: leaseWait}
}

var (
	// errPoolBusy: the pool did not have room in time. Retryable.
	errPoolBusy = errors.New("ingest budget busy")
	// errLeaseTooLarge: the request needs more than the whole pool. Permanent.
	errLeaseTooLarge = errors.New("request larger than the ingest budget")
)

// acquire leases n bytes, waiting up to p.wait (or until ctx ends) behind
// earlier waiters.
func (p *memPool) acquire(ctx context.Context, n int64) (*lease, error) {
	if n < 0 || n > p.size {
		return nil, errLeaseTooLarge
	}
	if !p.sem.TryAcquire(n) {
		if p.onBlock != nil {
			p.onBlock()
		}
		p.waits.Add(1)
		start := time.Now()
		wctx, cancel := context.WithTimeout(ctx, p.wait)
		err := p.sem.Acquire(wctx, n)
		cancel()
		p.waitNanos.Add(int64(time.Since(start)))
		if err != nil {
			p.timeouts.Add(1)
			return nil, errPoolBusy
		}
	}
	now := p.inUse.Add(n)
	for pk := p.peak.Load(); now > pk && !p.peak.CompareAndSwap(pk, now); pk = p.peak.Load() {
	}
	return &lease{p: p, held: n}, nil
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
