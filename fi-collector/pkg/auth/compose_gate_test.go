package auth

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// No private production hook is needed. The caller's Err/Done observations
// provide deterministic barriers; WithoutCancel ensures the worker cannot
// consume these caller-side observation ordinals.
type composeContext struct {
	context.Context
	calls            atomic.Int32
	at               int32
	entered, release chan struct{}
	gate             atomic.Bool
	waiting          chan struct{}
	once             sync.Once
}

func (c *composeContext) Err() error {
	if c.calls.Add(1) == c.at {
		close(c.entered)
		<-c.release
	}
	return c.Context.Err()
}
func (c *composeContext) Done() <-chan struct{} {
	if c.gate.Load() {
		c.once.Do(func() { close(c.waiting) })
	}
	return c.Context.Done()
}

func composeCacheGateCancellation(t *testing.T, phases []string) {
	for _, grant := range []bool{false, true} {
		for _, phase := range phases {
			for _, deadline := range []bool{false, true} {
				t.Run(map[bool]string{false: "personal/", true: "grant/"}[grant]+phase+map[bool]string{false: "/cancel", true: "/deadline"}[deadline], func(t *testing.T) {
					api, token, result, cfg := composeCredential(t, grant)
					resolver := composeResolver{publicationFunc: func(context.Context) (*ResolveResult, error) { return result, nil }, grant: func(context.Context, *GrantClaims) (*ResolveResult, error) { return result, nil }}
					a, err := NewWithResolver(cfg, resolver, nil, nil)
					if err != nil {
						t.Fatal(err)
					}
					key := CacheKey(api, token)
					if phase == "hit" {
						a.cache.putPositive(key, result)
					}
					base, cancel := context.WithCancel(context.Background())
					defer cancel()
					var expire context.CancelFunc
					if deadline {
						base, expire = context.WithTimeout(context.Background(), time.Second)
						defer expire()
					}
					// Miss/hit stop before first gate; flight stops after delivery;
					// observation stops after the admission gate has been released.
					at := int32(3)
					if phase == "delivered-flight" {
						at = 7
					}
					if phase == "post-observation" {
						at = 10
					}
					ctx := &composeContext{Context: base, at: at, entered: make(chan struct{}), release: make(chan struct{}), waiting: make(chan struct{})}
					done := make(chan composeAuthOutcome, 1)
					go func() { r, e := a.Authenticate(ctx, api, token); done <- composeAuthOutcome{r, e} }()
					composeWait(t, ctx.entered)
					if phase != "miss" {
						if e, _ := a.cache.peek(key); e == nil || e.result != result {
							close(ctx.release)
							t.Fatal("candidate was not live before gate contention")
						}
					}
					a.cache.mu.Lock()
					var unlock sync.Once
					defer unlock.Do(a.cache.mu.Unlock)
					if phase != "post-observation" {
						ctx.gate.Store(true)
						close(ctx.release)
						composeWait(t, ctx.waiting)
					}
					want := context.Canceled
					if deadline {
						composeWait(t, base.Done())
						want = context.DeadlineExceeded
					} else {
						cancel()
					}
					if phase == "post-observation" {
						close(ctx.release)
					}
					got := composeOutcome(t, done) // gate MUST still be held here
					if got.result != nil || !errors.Is(got.err, want) {
						t.Fatalf("canceled admission succeeded: %v", got.err)
					}
					if phase != "miss" {
						if e, _ := a.cache.peek(key); e == nil || e.result != result {
							t.Fatal("test invalidated the candidate instead of contending the gate")
						}
					}
					unlock.Do(a.cache.mu.Unlock)
					select {
					case <-done:
						t.Fatal("late second result")
					default:
					}
				})
			}
		}
	}
	t.Run("ready-token-canceled", func(t *testing.T) {
		var gate cacheMutex
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if !errors.Is(gate.LockContext(ctx), context.Canceled) {
			t.Fatal("already-canceled acquisition succeeded")
		}
		if err := gate.LockContext(context.Background()); err != nil {
			t.Fatal(err)
		}
		gate.Unlock()
		// Cancellation becomes visible specifically after the ready token wins.
		ctx2, cancel2 := context.WithCancel(context.Background())
		defer cancel2()
		wrapped := &composeContext{Context: ctx2, at: 2, entered: make(chan struct{}), release: make(chan struct{})}
		done := make(chan error, 1)
		go func() { done <- gate.LockContext(wrapped) }()
		composeWait(t, wrapped.entered)
		cancel2()
		close(wrapped.release)
		if !errors.Is(<-done, context.Canceled) {
			t.Fatal("post-acquire cancellation lost")
		}
		if err := gate.LockContext(context.Background()); err != nil {
			t.Fatal(err)
		}
		gate.Unlock()
	})
}
