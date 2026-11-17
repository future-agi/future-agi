package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestSurvivorUnionInvalidationAfterPublication(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, change := range []string{"invalidate", "same-scope-replace", "invalidate-both-attempts"} {
			t.Run(kind+"/"+change, func(t *testing.T) {
				api, token, old := survivorCredential(t, kind)
				fresh := refreshResult()
				fresh.OrgID, fresh.WorkspaceID, fresh.UserID = old.OrgID, old.WorkspaceID, old.UserID
				fresh.Principal, fresh.Grant = old.Principal, old.Grant
				var calls atomic.Int32
				resolve := publicationFunc(func(context.Context) (*ResolveResult, error) {
					if calls.Add(1) == 1 {
						return old, nil
					}
					return fresh, nil
				})
				r := survivorGrantResolver{publicationFunc: resolve, grant: func(ctx context.Context, _ *GrantClaims) (*ResolveResult, error) {
					return resolve(ctx)
				}}
				a, err := NewWithResolver(Config{Peppers: mustPeppers(t, goldenPepper)}, r, nil, nil)
				if err != nil {
					t.Fatal(err)
				}
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				first, second := newSurvivorBarrier(), newSurvivorBarrier()
				defer first.open()
				defer second.open()
				// Each cold attempt evaluates Done at stamp, receive, admission.
				// Its first Err after the receive is AFTER delivery, not before it.
				var pending *survivorBarrier
				probe := &survivorContextProbe{Context: ctx, onDone: func(n int32) {
					if n == 2 {
						pending = first
					} else if n == 5 && change == "invalidate-both-attempts" {
						pending = second
					}
				}, beforeErr: func(int32) {
					if pending != nil {
						b := pending
						pending = nil
						b.stop()
					}
				}}
				key := CacheKey(api, token)
				done := survivorAuthenticate(a, probe, api, token)
				survivorWait(t, first.entered, "first worker publication and delivery")
				entry, status := a.cache.peek(key)
				if status != "fresh" || entry.result != old || calls.Load() != 1 {
					t.Fatal("invalidation must follow publication of the exact old result")
				}
				if change == "same-scope-replace" {
					// No generation bump: this specifically requires pointer identity,
					// not just equal scope, claims, or a generation comparison.
					entry = survivorPublish(t, a.cache, key, fresh)
				} else {
					a.InvalidateKey(key)
				}
				first.open()
				if change == "invalidate-both-attempts" {
					survivorWait(t, second.entered, "second worker publication and delivery")
					entry, status = a.cache.peek(key)
					if status != "fresh" || entry.result != fresh || calls.Load() != 2 {
						t.Fatal("second invalidation must follow a fresh second publication")
					}
					a.InvalidateKey(key)
					second.open()
				}
				got := survivorReceive(t, done)
				switch change {
				case "invalidate-both-attempts":
					var changed *ProjectAuthChangedError
					if got.result != nil || !errors.As(got.err, &changed) || calls.Load() != 2 {
						t.Fatalf("post-publication retry bound lost: calls=%d err=%v", calls.Load(), got.err)
					}
					survivorResident(t, a.cache, key, nil)
				case "same-scope-replace":
					if got.result != fresh || got.err != nil || calls.Load() != 1 {
						t.Fatalf("waiter admitted/re-published replaced result: calls=%d err=%v", calls.Load(), got.err)
					}
					survivorResident(t, a.cache, key, entry)
				default:
					if got.result != fresh || got.err != nil || calls.Load() != 2 {
						t.Fatalf("waiter bypassed fresh retry: calls=%d err=%v", calls.Load(), got.err)
					}
					entry, status = a.cache.peek(key)
					if status != "fresh" || entry.result != fresh {
						t.Fatal("old waiter overwrote fresh publication")
					}
				}
			})
		}
	}
}

func TestSurvivorUnionProductionPublicationLifetime(t *testing.T) {
	for _, mode := range []string{"aged-query", "expired-grant", "nil-result", "global-fence", "key-fence"} {
		t.Run(mode, func(t *testing.T) {
			c := newCache(5*time.Second, 0)
			resident := survivorPublish(t, c, "key", refreshResult())
			stamp, epoch, err := c.validationStamp(context.Background(), "key")
			if err != nil {
				t.Fatal(err)
			}
			candidate := refreshResult()
			wantPublished, wantErr := false, error(nil)
			switch mode {
			case "aged-query":
				// Logical pre-query time: no sleep and no fixture-only putPositive.
				stamp.started = time.Now().Add(-c.ttl)
			case "expired-grant":
				candidate.Grant = &GrantClaims{Exp: time.Now().Unix()}
				wantErr = ErrUnauthenticated
			case "nil-result":
				candidate, wantPublished = nil, true // No negative Store/delete.
			case "global-fence":
				stamp.generation++
			case "key-fence":
				epoch++
			}
			published, err := c.publishValidated(context.Background(), "key", candidate, epoch, stamp)
			if published != wantPublished || !errors.Is(err, wantErr) {
				t.Fatalf("production publication fence: published=%v err=%v", published, err)
			}
			survivorResident(t, c, "key", resident)
			// A successful production publication retains query-start time exactly.
			stamp, epoch, err = c.validationStamp(context.Background(), "other")
			if err != nil {
				t.Fatal(err)
			}
			stamp.started = stamp.started.Add(-time.Second)
			positive := refreshResult()
			published, err = c.publishValidated(context.Background(), "other", positive, epoch, stamp)
			raw, exists := c.m.Load("other")
			if !published || err != nil || !exists || raw.(*cacheEntry).result != positive || !raw.(*cacheEntry).fetchedAt.Equal(stamp.started) {
				t.Fatal("production publication reset the query-start age")
			}
		})
	}
}

func TestSurvivorUnionGrantExpiresAtPublicationGate(t *testing.T) {
	c := newCache(30*time.Second, 0)
	resident := survivorPublish(t, c, "key", refreshResult())
	stamp, epoch, err := c.validationStamp(context.Background(), "key")
	if err != nil {
		t.Fatal(err)
	}
	candidate := refreshResult()
	candidate.Grant = &GrantClaims{Exp: time.Now().Add(1500 * time.Millisecond).Unix()}
	base, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered, release := make(chan struct{}), newSurvivorBarrier()
	defer release.open()
	probe := &survivorContextProbe{Context: base, onDone: func(n int32) {
		if n == 1 {
			close(entered)
			<-release.release
		}
	}}
	c.mu.Lock()
	held := true
	defer func() {
		if held {
			c.mu.Unlock()
		}
	}()
	type publication struct {
		ok  bool
		err error
	}
	done := make(chan publication, 1)
	go func() {
		ok, err := c.publishValidated(probe, "key", candidate, epoch, stamp)
		done <- publication{ok, err}
	}()
	survivorWait(t, entered, "publication gate with a live grant")
	if time.Now().Unix() >= candidate.Grant.Exp {
		t.Fatal("grant expired before reaching the held publication gate")
	}
	// Wait for the actual expiry boundary, not an arbitrary scheduling sleep;
	// neither the immutable candidate nor any cache clock is replaced.
	survivorExpiryBoundary(t, candidate.Grant.Exp)
	survivorResident(t, c, "key", resident)
	c.mu.Unlock()
	held = false
	release.open()
	select {
	case got := <-done:
		if got.ok || !errors.Is(got.err, ErrUnauthenticated) {
			t.Fatalf("grant expired at publication gate was stored: %v", got.err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("publication did not settle")
	}
	survivorResident(t, c, "key", resident)
}
