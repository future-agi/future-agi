package ratelimit

import (
	"sync"
	"time"
)

// Limiter defines the rate limiting interface.
// Both the in-memory RateLimiter and the Redis-backed redisstate.RateLimiter
// satisfy this interface, making the backend pluggable.
type Limiter interface {
	Allow(key string, limit int) (allowed bool, remaining int, resetSeconds int)
	Stop()
}

// TokenLimiter is implemented by a limiter that can also hold a token budget.
//
// Tokens cannot be counted in advance: a request's cost is known only once the
// provider has answered. So the budget is checked before the call and charged
// after it, which lets one request cross the limit before the next is
// refused. A limit meant to bound spend tolerates that; a limit meant to bound
// a single request does not, and is not what this is.
type TokenLimiter interface {
	// AllowTokens reports whether the window still has budget. It charges
	// nothing: AddTokens does that once the cost is known.
	AllowTokens(key string, limit int) (allowed bool, remaining int, resetSeconds int)

	// AddTokens charges tokens against the window.
	AddTokens(key string, tokens int)
}

const (
	windowDuration  = 60 * time.Second
	cleanupInterval = 5 * time.Minute
	staleThreshold  = 2 * windowDuration // evict entries idle for 2 full windows
)

type windowState struct {
	count       int64
	windowStart time.Time
}

// RateLimiter enforces per-key requests-per-minute limits using fixed windows.
type RateLimiter struct {
	mu      sync.Mutex
	windows map[string]*windowState
	stopCh  chan struct{}
}

// NewRateLimiter creates a new in-memory rate limiter with periodic cleanup.
func NewRateLimiter() *RateLimiter {
	rl := &RateLimiter{
		windows: make(map[string]*windowState),
		stopCh:  make(chan struct{}),
	}
	go rl.cleanupLoop()
	return rl
}

// Stop halts the background cleanup goroutine.
func (r *RateLimiter) Stop() {
	close(r.stopCh)
}

func (r *RateLimiter) cleanupLoop() {
	ticker := time.NewTicker(cleanupInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			r.evictStale()
		case <-r.stopCh:
			return
		}
	}
}

func (r *RateLimiter) evictStale() {
	now := time.Now()
	r.mu.Lock()
	defer r.mu.Unlock()
	for key, w := range r.windows {
		if now.Sub(w.windowStart) > staleThreshold {
			delete(r.windows, key)
		}
	}
}

// window returns the key's current window, starting a new one when the last
// has run out. Callers hold r.mu.
func (r *RateLimiter) window(key string, now time.Time) (w *windowState, resetSeconds int) {
	w, ok := r.windows[key]
	if !ok {
		w = &windowState{windowStart: now}
		r.windows[key] = w
	}

	elapsed := now.Sub(w.windowStart)
	if elapsed >= windowDuration {
		w.count = 0
		w.windowStart = now
		elapsed = 0
	}

	resetSeconds = int((windowDuration - elapsed).Seconds())
	if resetSeconds < 1 {
		resetSeconds = 1
	}
	return w, resetSeconds
}

func remainingUnder(limit int, count int64) int {
	remaining := int(int64(limit) - count)
	if remaining < 0 {
		remaining = 0
	}
	return remaining
}

// Allow checks whether a request is allowed under the given RPM limit.
// Returns (allowed, remaining, resetSeconds).
func (r *RateLimiter) Allow(key string, limit int) (bool, int, int) {
	now := time.Now()

	r.mu.Lock()
	defer r.mu.Unlock()

	w, resetSeconds := r.window(key, now)
	if w.count >= int64(limit) {
		return false, 0, resetSeconds
	}

	w.count++
	return true, remainingUnder(limit, w.count), resetSeconds
}

// AllowTokens reports whether the key's token window still has budget.
func (r *RateLimiter) AllowTokens(key string, limit int) (bool, int, int) {
	now := time.Now()

	r.mu.Lock()
	defer r.mu.Unlock()

	w, resetSeconds := r.window(key, now)
	if w.count >= int64(limit) {
		return false, 0, resetSeconds
	}
	return true, remainingUnder(limit, w.count), resetSeconds
}

// AddTokens charges tokens against the key's token window.
func (r *RateLimiter) AddTokens(key string, tokens int) {
	if tokens <= 0 {
		return
	}
	now := time.Now()

	r.mu.Lock()
	defer r.mu.Unlock()

	w, _ := r.window(key, now)
	w.count += int64(tokens)
}
