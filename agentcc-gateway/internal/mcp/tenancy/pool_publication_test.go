package tenancy

import (
	"context"
	"encoding/json"
	"maps"
	"sync"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// publicationClient pauses after connect's final authorization check. Its
// ListTools counter also observes idle refreshes without making a user call.
type publicationClient struct {
	*fakeClient
	listing chan struct{}
	release chan struct{}
	once    sync.Once
	lists   atomic.Int32
}

func (c *publicationClient) ListTools(ctx context.Context) ([]json.RawMessage, error) {
	c.lists.Add(1)
	c.once.Do(func() {
		close(c.listing)
		// A completed upstream response can race cancellation. Deliberately
		// return it even after revoke so entry retirement must reject it.
		<-c.release
	})
	return c.fakeClient.ListTools(ctx)
}

func TestPoolApplyCredentialPublication(t *testing.T) {
	for _, phase := range []string{"revoke_before_publication", "revoke_after_publication", "no_revoke_control"} {
		t.Run(phase, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				paused, publish := make(chan struct{}), make(chan struct{})
				r := newRig(t, Options{
					InitialWait: -1,
					Publish: func(id string, c mcp.UpstreamClient) bool {
						if id == "publication-barrier" && c == nil {
							close(paused)
							<-publish
						}
						return false
					},
				})
				u := r.factory.upstream(urlA, "search")
				r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
				_, pool := r.resolve("org-a")
				p := pool.(*Pool)
				synctest.Wait()
				if s, _ := p.Server("github"); !s.Healthy {
					t.Fatal("initial credential did not connect")
				}

				late := &publicationClient{
					fakeClient: &fakeClient{up: u},
					listing:    make(chan struct{}),
					release:    make(chan struct{}),
				}
				var builds atomic.Int32
				d := *r.deps.Load()
				d.ClientFactory = func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
					builds.Add(1)
					return late, nil
				}
				r.deps.Store(&d)
				r.push("org-a", 2, credentialConfig("active", "synthetic-second", ""))
				cfg := r.store.Get("org-a")
				specs, errs := orgSpecs(cfg, nil)
				sp := specs["github"]
				sp.cacheTTLSeconds = 1
				specs["github"] = sp

				// The removal callback is a test-only scheduling barrier after
				// apply constructs next, but before entries.Store. This inert
				// static sentinel avoids adding a production scheduling hook or
				// depending on map iteration order, sleeps, or mutex fairness.
				p.mu.Lock()
				cur := maps.Clone(p.current())
				cur["publication-barrier"] = newEntry(p, spec{id: "publication-barrier", source: SourceStatic})
				p.entries.Store(&cur)
				p.mu.Unlock()
				applied := make(chan error, 1)
				go func() { applied <- p.apply(specs, errs, cfg, nil) }()
				<-paused
				// Before the fix the unpublished replacement reaches ListTools
				// and blocks there. After the fix it has not started at all.
				synctest.Wait()
				if phase == "revoke_before_publication" {
					r.push("org-a", 3, credentialConfig("revoked", "", ""))
				}
				close(publish)
				if err := <-applied; err != nil {
					t.Fatal(err)
				}
				if phase != "revoke_before_publication" {
					<-late.listing
					if phase == "revoke_after_publication" {
						r.push("org-a", 3, credentialConfig("revoked", "", ""))
					}
				}
				close(late.release)
				synctest.Wait()

				// No Resolve, Client, or guarded dispatch follows the revoke:
				// those would authorize again and conceal an idle-session leak.
				e := p.current()["github"]
				before := late.lists.Load()
				if phase == "no_revoke_control" {
					if builds.Load() != 1 || e.isRetired() || !late.Healthy() {
						t.Fatal("active replacement must connect and remain open")
					}
				} else {
					if !e.isRetired() || e.ctx.Err() == nil {
						t.Error("revoked replacement survived publication without retirement or cancellation")
					}
					if builds.Load() > 0 && (!late.closed.Load() || late.closeCall.Load() != 1) {
						t.Error("revoked replacement's idle upstream was not closed exactly once")
					}
					if s, _ := p.Server("github"); s.Healthy {
						t.Error("revoked replacement still reports a healthy session")
					}
				}
				// synctest advances virtual time, exercising the real refreshLoop
				// without wall-clock polling or a provider/network dependency.
				time.Sleep(3 * time.Second)
				synctest.Wait()
				after := late.lists.Load()
				if phase == "no_revoke_control" {
					if after <= before {
						t.Fatal("control did not exercise background upstream listing")
					}
				} else if after != before {
					t.Errorf("background upstream listing continued after revoke: before=%d after=%d", before, after)
				}
			})
		})
	}
}
