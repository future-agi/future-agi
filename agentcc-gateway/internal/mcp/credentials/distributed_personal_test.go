package credentials

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A successful eviction through either resolver must fence the personal grant
// at the resolver that owns a pending response or retained upstream handle.
func TestCredentialLifecyclePersonalEvictionAcrossResolvers(t *testing.T) {
	for _, user := range []string{"u1", ""} {
		t.Run("purge-user="+user, func(t *testing.T) {
			for _, pending := range []bool{true, false} {
				name := "idle-handle"
				if pending {
					name = "pending-fetch"
				}
				t.Run(name, func(t *testing.T) {
					rig := newLifecycleRig(t)
					r := rig.resolver
					purger, err := NewResolver(ResolverOptions{Tokens: rig.source, Cache: r.cache, Codec: testCodec(t)})
					if err != nil {
						t.Fatal(err)
					}
					if !pending {
						prior := rig.client(t, "org-a", "u1")
						rig.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
						if _, err := purger.PurgeCredentials(context.Background(), "org-a", user, "issues"); err != nil {
							t.Fatal(err)
						}
						if lifecycleCall(prior) == nil {
							t.Error("retained personal handle remained callable after another resolver confirmed eviction")
						}
						return
					}
					rig.source.started, rig.source.release = make(chan struct{}, 1), make(chan struct{})
					type result struct {
						auth   *mcp.AuthOverride
						denial *mcp.Denial
					}
					done := make(chan result, 1)
					go func() {
						auth, _, d := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
						done <- result{auth, d}
					}()
					<-rig.source.started
					rig.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
					_, err = purger.PurgeCredentials(context.Background(), "org-a", user, "issues")
					close(rig.source.release)
					got := <-done
					if err != nil {
						t.Fatal(err)
					}
					if got.denial == nil || got.auth != nil {
						t.Error("pre-eviction personal response remained publishable after another resolver confirmed eviction")
					}
					_, ok, err := r.cache.Get(context.Background(), keyA1)
					if err != nil {
						t.Fatal(err)
					}
					if ok {
						t.Error("pre-eviction personal response repopulated the cache after eviction")
					}
				})
			}
		})
	}
}

func TestCredentialLifecycleOutstandingFetchExpiresBeforePublication(t *testing.T) {
	for _, shared := range []bool{false, true} {
		name := "personal"
		if shared {
			name = "shared"
		}
		t.Run(name, func(t *testing.T) {
			rig := newLifecycleRig(t)
			r := rig.resolver
			start := time.Now()
			var elapsed atomic.Int64
			r.now = func() time.Time { return start.Add(time.Duration(elapsed.Load())) }
			started, release := make(chan struct{}), make(chan struct{})
			r.tokens = lifecycleFetch(func(context.Context, Key) (Token, error) {
				close(started)
				<-release
				return Token{AccessToken: "synthetic-expiring", ExpiresAt: start.Add(time.Hour), CredentialVersion: 1}, nil
			})
			done := make(chan bool, 1)
			go func() {
				if shared {
					h, err := r.SharedAuth("org-a", "issues").Header(context.Background())
					done <- err != nil && h == nil
				} else {
					auth, _, d := r.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
					done <- d != nil && auth == nil
				}
			}()
			<-started
			elapsed.Store(int64(2 * time.Hour))
			close(release)
			if !<-done {
				t.Error("grant that expired during its fetch was published")
			}
		})
	}
}

func TestCredentialLifecycleShortRefreshedGrantDispatches(t *testing.T) {
	for _, ttl := range []time.Duration{90 * time.Second, 2 * time.Minute} {
		t.Run(ttl.String(), func(t *testing.T) {
			rig := newLifecycleRig(t)
			prior := rig.client(t, "org-a", "u1")
			source := &shortRefreshSource{resolver: rig.resolver, ttl: ttl}
			rig.resolver.tokens = source
			fresh := rig.client(t, "org-a", "u1")
			if lifecycleCall(fresh) != nil {
				t.Error("refreshed short-lived grant failed upstream dispatch")
			}
			if lifecycleCall(prior) == nil {
				t.Error("prior grant remained callable after short-lived refresh")
			}
			if source.refreshes.Load() != 1 || source.checks.Load() != 1 {
				t.Error("short-lived refresh was not bounded to one refresh and one current-state check")
			}
		})
	}
}
