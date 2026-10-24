package ratelimit

import (
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// allowOnlyLimiter is a Limiter that cannot check several counters at once
// (not a redisstate.MultiLimiter), so New gives the org's limits an in-memory
// limiter of its own.
type allowOnlyLimiter struct{ stopped atomic.Int32 }

func (l *allowOnlyLimiter) Allow(string, int) (bool, int, int) { return true, 0, 0 }
func (l *allowOnlyLimiter) Stop()                              { l.stopped.Add(1) }

func isStopped(rl *RateLimiter) bool {
	select {
	case <-rl.stopCh:
		return true
	default:
		return false
	}
}

// stopPlugin calls the plugin's Stop, or fails the test when it has none;
// own is stopped either way, so the test itself leaks no goroutine.
func stopPlugin(t *testing.T, p *Plugin, own ...*RateLimiter) {
	t.Helper()
	s, ok := any(p).(interface{ Stop() })
	if !ok {
		for _, rl := range own {
			if !isStopped(rl) {
				rl.Stop()
			}
		}
		t.Fatal("the plugin has no Stop: the in-memory limiter New made itself runs its cleanup goroutine for ever")
	}
	s.Stop()
}

// The plugin stops the in-memory limiters New made itself, once however often
// it is stopped, and never the limiter its caller passed in.
func TestPluginStopStopsOnlyItsOwnLimiters(t *testing.T) {
	cfg := config.RateLimitConfig{Enabled: true}

	// A limiter that cannot check several counters: the org's limits get an
	// in-memory limiter of the plugin's own.
	caller := &allowOnlyLimiter{}
	p := New(cfg, nil, caller)
	own, ok := p.multi.(*RateLimiter)
	if !ok {
		t.Fatalf("org limiter for an Allow-only limiter is %T, want the plugin's own *RateLimiter", p.multi)
	}
	stopPlugin(t, p, own)
	if !isStopped(own) {
		t.Error("Stop left the in-memory limiter New made for the org's limits running")
	}
	if n := caller.stopped.Load(); n != 0 {
		t.Errorf("Stop stopped the caller's limiter %d time(s), want 0", n)
	}
	stopPlugin(t, p, own) // a second Stop is harmless (no close of a closed channel)

	// No limiter given: New made the one limiter, and Stop stops it.
	p = New(cfg, nil, nil)
	made, ok := p.limiter.(*RateLimiter)
	if !ok {
		t.Fatalf("default limiter is %T, want *RateLimiter", p.limiter)
	}
	stopPlugin(t, p, made)
	if !isStopped(made) {
		t.Error("Stop left the limiter New made (no limiter given) running")
	}

	// The caller's own multi-capable limiter is the caller's to stop.
	callers := NewRateLimiter()
	t.Cleanup(func() {
		if !isStopped(callers) {
			callers.Stop()
		}
	})
	p = New(cfg, nil, callers)
	stopPlugin(t, p)
	if isStopped(callers) {
		t.Error("Stop stopped the in-memory limiter the caller passed in")
	}
}
