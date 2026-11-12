package tenancy

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type r7ClientResult struct {
	client mcp.UpstreamClient
	err    error
}

func r7Result(t *testing.T, ch <-chan r7ClientResult) r7ClientResult {
	t.Helper()
	select {
	case r := <-ch:
		return r
	case <-time.After(5 * time.Second):
		t.Fatal("client construction did not finish")
		return r7ClientResult{}
	}
}

// Pause the SECOND transport grant check, not entry acquisition or the first
// pre-credential check. Nothing has reached the loopback peer at that point.
// A catalog-only flip does not cancel the lifetime: the final admission check
// itself must observe it and retire the entry. The unchanged-grant control
// proves that the pause is on a real, otherwise successful HTTP/SSE send.
func TestK2R7FinalGrantFence(t *testing.T) {
	for _, transport := range []string{"http", "sse"} {
		for _, refuse := range []bool{false, true} {
			name := "enabled"
			if refuse {
				name = "refused"
			}
			t.Run(transport+"/"+name, func(t *testing.T) {
				f := newR6Fixture(t, "google-docs", "1.0.2", transport, "positive", "catalog")
				paused, release := make(chan struct{}), make(chan struct{})
				var releaseOnce sync.Once
				unblock := func() { releaseOnce.Do(func() { close(release) }) }
				t.Cleanup(unblock)
				var inFactory atomic.Bool
				var checks atomic.Int64
				f.auth.Check = func(context.Context) error {
					if inFactory.Load() && checks.Add(1) == 2 {
						close(paused)
						<-release
					}
					return nil // the pinned grant is still valid after the pause
				}
				d := *f.rig.deps.Load()
				real := d.ClientFactory
				d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
					inFactory.Store(true)
					defer inFactory.Store(false)
					return real(cfg)
				}
				f.rig.deps.Store(&d)
				done := make(chan r7ClientResult, 1)
				go func() { c, err := f.pool.Client("service", "person", f.auth); done <- r7ClientResult{c, err} }()
				if !r6Wait(t, paused, "final transport grant check") {
					return
				}
				if checks.Load() != 2 || f.wire.authenticated.Load() != 0 {
					t.Fatal("pause was not before the first authenticated send's final grant check")
				}
				if refuse {
					f.wire.flip() // live catalog change only, no store invalidation
					if f.entry.isRetired() || f.entry.ctx.Err() != nil {
						t.Fatal("fixture retired before the transport could evaluate current policy")
					}
				}
				unblock()
				out := r7Result(t, done)
				if refuse {
					r6ComingSoon(t, out.err)
					if out.client != nil || !f.entry.isRetired() || f.entry.ctx.Err() == nil {
						t.Error("refused construction published a client or did not retire its lifetime")
					}
					if f.wire.authenticated.Load() != 0 || f.wire.after.Load() != 0 {
						t.Error("final grant validation released an authenticated request after catalog refusal")
					}
				} else {
					if out.err != nil || out.client == nil {
						t.Fatalf("enabled control failed: %v", out.err)
					}
					if _, err := out.client.CallTool(context.Background(), "echo", nil, mcp.CallOptions{}); err != nil {
						t.Fatal(err)
					}
					if f.wire.calls.Load() != 1 || f.wire.authenticated.Load() == 0 || f.entry.isRetired() {
						t.Fatal("enabled control did not dispatch normally")
					}
				}
			})
		}
	}
}

// Done is observed only after the constructor has entered the paused factory.
// At that point the second caller's select is the only new parent-Done access.
// This avoids sleeps and makes the cancellation branch deterministic: the
// factory cannot publish build.done until the test releases its return.
type r7WaitContext struct {
	context.Context
	armed atomic.Bool
	once  sync.Once
	seen  chan struct{}
}

func (c *r7WaitContext) Done() <-chan struct{} {
	if c.armed.Load() {
		c.once.Do(func() { close(c.seen) })
	}
	return c.Context.Done()
}

func TestK2R7ConstructionWaiter(t *testing.T) {
	for _, refuse := range []bool{false, true} {
		name := "enabled"
		if refuse {
			name = "refused"
		}
		t.Run(name, func(t *testing.T) {
			f := newR6Fixture(t, "google-docs", "1.0.2", "http", "positive", "catalog")
			if !r6Wait(t, f.entry.firstDone, "per-user entry startup") {
				return
			}
			observed := &r7WaitContext{Context: f.entry.ctx, seen: make(chan struct{})}
			f.entry.ctx = observed // startup is complete; no expiry watcher for this fixture
			entered, start, built, finish := make(chan struct{}), make(chan struct{}), make(chan struct{}), make(chan struct{})
			var startOnce, finishOnce sync.Once
			unstart := func() { startOnce.Do(func() { close(start) }) }
			unfinish := func() { finishOnce.Do(func() { close(finish) }) }
			t.Cleanup(func() { unstart(); unfinish() })
			var factories atomic.Int64
			d := *f.rig.deps.Load()
			real := d.ClientFactory
			d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
				if factories.Add(1) == 1 {
					close(entered)
				}
				<-start
				c, err := real(cfg)
				close(built)
				<-finish
				return c, err
			}
			f.rig.deps.Store(&d)
			builder, waiter := make(chan r7ClientResult, 1), make(chan r7ClientResult, 1)
			go func() { c, err := f.pool.Client("service", "person", f.auth); builder <- r7ClientResult{c, err} }()
			if !r6Wait(t, entered, "factory entry") {
				return
			}
			observed.armed.Store(true)
			go func() { c, err := f.pool.Client("service", "person", f.auth); waiter <- r7ClientResult{c, err} }()
			if !r6Wait(t, observed.seen, "singleflight waiter cancellation select") {
				return
			}
			observed.armed.Store(false)
			if refuse {
				f.wire.flip()
			}
			unstart()
			if !r6Wait(t, built, "real factory result") {
				return
			}
			if refuse {
				// build.done is still open: retirement must wake the waiter, and
				// reauthorization must preserve Coming soon instead of needs_reauth.
				waiting := r7Result(t, waiter)
				r6ComingSoon(t, waiting.err)
				if waiting.client != nil || !f.entry.isRetired() || f.wire.authenticated.Load() != 0 {
					t.Error("waiter refusal did not preserve retirement and zero sends")
				}
				unfinish()
				constructing := r7Result(t, builder)
				r6ComingSoon(t, constructing.err)
			} else {
				select {
				case <-waiter:
					t.Fatal("enabled waiter escaped before construction completed")
				default:
				}
				unfinish()
				for _, ch := range []<-chan r7ClientResult{builder, waiter} {
					out := r7Result(t, ch)
					if out.err != nil || out.client == nil {
						t.Fatalf("enabled caller failed: %v", out.err)
					}
				}
				if f.wire.authenticated.Load() == 0 || f.entry.isRetired() {
					t.Error("enabled construction did not connect")
				}
			}
			if factories.Load() != 1 {
				t.Error("the two callers did not share one construction")
			}
		})
	}
}
