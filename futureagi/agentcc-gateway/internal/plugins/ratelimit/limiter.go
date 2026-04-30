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

// Allow checks whether a request is allowed under the given RPM limit.
// Returns (allowed, remaining, resetSeconds).
func (r *RateLimiter) Allow(key string, limit int) (bool, int, int) {
	now := time.Now()

	r.mu.Lock()
	defer r.mu.Unlock()

	w, ok := r.windows[key]
	if !ok {
		w = &windowState{windowStart: now}
		r.windows[key] = w
	}

	// Reset window if expired.
	elapsed := now.Sub(w.windowStart)
	if elapsed >= windowDuration {
		w.count = 0
		w.windowStart = now
		elapsed = 0
	}

	resetSeconds := int((windowDuration - elapsed).Seconds())
	if resetSeconds < 1 {
		resetSeconds = 1
	}

	if w.count >= int64(limit) {
		return false, 0, resetSeconds
	}

	w.count++
	remaining := int(int64(limit) - w.count)
	if remaining < 0 {
		remaining = 0
	}

	return true, remaining, resetSeconds
}
