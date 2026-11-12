package auth

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func composeInvalidators(a *Authenticator, key, api string) map[string]func() {
	return map[string]func(){
		"digest": func() { a.cache.evictAPIKey(APIKeyDigest(api)) },
		"direct": func() { a.InvalidateKey(key) },
		"legacy": func() { a.evictCredential(key) },
		"grant":  func() { a.evictCredential("grant:uncached") },
		"flush":  a.cache.flush,
		"forget": func() { a.forget(key) },
	}
}

func TestComposeGlobalFenceCoversUncachedDigestAndDirectInvalidation(t *testing.T) {
	for _, invalidator := range []string{"digest", "direct", "legacy", "grant", "flush", "forget"} {
		t.Run(invalidator, func(t *testing.T) {
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			old, fresh := refreshResult(), refreshResult()
			var calls atomic.Int32
			a := publicationAuth(t, func(ctx context.Context) (*ResolveResult, error) {
				if calls.Add(1) == 1 {
					close(entered)
					select {
					case <-release:
					case <-ctx.Done():
						return nil, ctx.Err()
					}
					return old, nil
				}
				return fresh, nil
			})
			key := CacheKey("k", "s")
			done := make(chan composeAuthOutcome, 1)
			go func() { r, e := a.Authenticate(context.Background(), "k", "s"); done <- composeAuthOutcome{r, e} }()
			composeWait(t, entered)
			composeInvalidators(a, key, "k")[invalidator]()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			r, e := a.Authenticate(ctx, "k", "s")
			if e != nil || r != fresh {
				t.Fatalf("post-notice request joined stale flight: %v", e)
			}
			once.Do(func() { close(release) })
			got := composeOutcome(t, done)
			if got.result == old || got.err != nil {
				t.Fatalf("stale completion admitted or fresh retry failed: %v", got.err)
			}
			if e, _ := a.cache.peek(key); e == nil || e.result != fresh {
				t.Fatal("old worker/waiter replaced fresh result")
			}
			before := a.cache.generation.Load()
			a.cache.evictAPIKey("unrelated-digest")
			if a.cache.generation.Load() == before {
				t.Fatal("uncached digest failed to advance global fence")
			}
			if e, _ := a.cache.peek(key); e == nil || e.result != fresh {
				t.Fatal("unrelated resident entry invalidated")
			}
		})
	}
}

func TestComposeGlobalFenceSubsumesKeyFence(t *testing.T) {
	for _, present := range []bool{false, true} {
		for _, name := range []string{"digest", "direct", "legacy", "grant", "flush", "forget"} {
			t.Run(name+map[bool]string{false: "/absent", true: "/resident"}[present], func(t *testing.T) {
				a := publicationAuth(t, func(context.Context) (*ResolveResult, error) { return nil, nil })
				key := CacheKey("k", "s")
				result := refreshResult()
				result.APIKeyDigest = APIKeyDigest("k")
				if present {
					a.cache.putPositive(key, result)
				}
				stamp, epoch, err := a.cache.validationStamp(context.Background(), key)
				if err != nil {
					t.Fatal(err)
				}
				composeInvalidators(a, key, "k")[name]()
				next, nextEpoch, err := a.cache.validationStamp(context.Background(), key)
				if err != nil || next.generation == stamp.generation {
					t.Fatalf("global stamp unchanged: %v", err)
				}
				if name != "grant" && (name != "digest" || present) && epoch == nextEpoch {
					t.Fatal("affected key stripe unchanged")
				}
				ok, err := a.cache.publishValidated(context.Background(), key, result, epoch, stamp)
				if err != nil || ok {
					t.Fatalf("old stamp published: %v", err)
				}
			})
		}
	}
}

func TestComposeCacheGateCancellation(t *testing.T) {
	composeCacheGateCancellation(t, []string{"hit", "delivered-flight", "miss", "post-observation"})
}

func TestComposeNoCacheLockOrderInversion(t *testing.T) {
	if typ := reflect.TypeOf(keyGeneration{}); typ.NumField() != 1 || typ.Field(0).Name != "epoch" {
		t.Fatal("stripe acquired state beyond its bounded epoch")
	}
	for _, name := range []string{"digest", "direct", "flush"} {
		for _, phase := range []string{"after-stamp", "publication-owns-gate"} {
			t.Run(name+"/"+phase, func(t *testing.T) {
				a := publicationAuth(t, func(context.Context) (*ResolveResult, error) { return nil, nil })
				c, key := a.cache, CacheKey("k", "s")
				result := refreshResult()
				result.APIKeyDigest = APIKeyDigest("k")
				c.putPositive(key, result)
				base, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				defer cancel()
				ctx := &composeContext{Context: base, at: 3, entered: make(chan struct{}), release: make(chan struct{})}
				var release sync.Once
				defer release.Do(func() { close(ctx.release) })
				stamp, epoch, err := c.validationStamp(base, key)
				if err != nil {
					t.Fatal(err)
				}
				published := make(chan bool, 1)
				if phase == "after-stamp" {
					go func() {
						s, e, err := c.validationStamp(ctx, key)
						if err != nil {
							published <- false
							return
						}
						ok, err := c.publishValidated(base, key, result, e, s)
						published <- ok && err == nil
					}()
				} else {
					ctx.at = 2 // LockContext acquired the only gate, before checking cancellation.
					go func() { ok, err := c.publishValidated(ctx, key, result, epoch, stamp); published <- ok && err == nil }()
				}
				composeWait(t, ctx.entered)
				invalidating, invalidated := make(chan struct{}), make(chan struct{})
				go func() { close(invalidating); composeInvalidators(a, key, "k")[name](); close(invalidated) }()
				composeWait(t, invalidating)
				if phase == "after-stamp" {
					composeWait(t, invalidated)
				} else {
					if len(c.mu.held) != 1 {
						t.Fatal("publication barrier does not own gate")
					}
					select {
					case <-invalidated:
						t.Fatal("invalidator bypassed publication gate")
					default:
					}
				}
				release.Do(func() { close(ctx.release) })
				select {
				case ok := <-published:
					if ok != (phase == "publication-owns-gate") {
						t.Fatal("publication ordering not serialized")
					}
				case <-base.Done():
					t.Fatal("publication did not settle")
				}
				composeWait(t, invalidated)
				if e, _ := c.peek(key); e != nil {
					t.Fatal("ordered invalidation left old entry")
				}
			})
		}
	}
}

func TestComposeOldNegativeCannotDeleteReplacement(t *testing.T) {
	for _, negative := range []bool{false, true} {
		t.Run(map[bool]string{false: "positive", true: "nil"}[negative], func(t *testing.T) {
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			old, fresh := refreshResult(), refreshResult()
			var calls atomic.Int32
			a := publicationAuth(t, func(ctx context.Context) (*ResolveResult, error) {
				if calls.Add(1) == 1 {
					close(entered)
					select {
					case <-release:
					case <-ctx.Done():
						return nil, ctx.Err()
					}
					if negative {
						return nil, nil
					}
					return old, nil
				}
				return fresh, nil
			})
			key := CacheKey("k", "s")
			done := make(chan composeAuthOutcome, 1)
			go func() { r, e := a.Authenticate(context.Background(), "k", "s"); done <- composeAuthOutcome{r, e} }()
			composeWait(t, entered)
			a.InvalidateKey(key)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			if r, e := a.Authenticate(ctx, "k", "s"); e != nil || r != fresh {
				t.Fatalf("replacement unavailable: %v", e)
			}
			replacement, _ := a.cache.m.Load(key)
			once.Do(func() { close(release) })
			got := composeOutcome(t, done)
			if got.err != nil || got.result != fresh {
				t.Fatalf("late completion did not retry current result: %v", got.err)
			}
			if stored, _ := a.cache.m.Load(key); stored != replacement {
				t.Fatal("late worker or waiter changed replacement entry")
			}
			if r, e := a.Authenticate(ctx, "k", "s"); e != nil || r != fresh || calls.Load() != 2 {
				t.Fatalf("replacement lost or negative cached: %v calls=%d", e, calls.Load())
			}
		})
	}
	t.Run("expired-reader-cleanup", func(t *testing.T) {
		c := newCache(time.Second, 0)
		old := &cacheEntry{result: refreshResult(), fetchedAt: time.Now().Add(-2 * time.Second)}
		c.m.Store("key", old)
		observed, _ := c.m.Load("key")
		if observed.(*cacheEntry).fresh(c.ttl) {
			t.Fatal("reader did not observe expiry")
		}
		fresh := refreshResult()
		c.putPositive("key", fresh)
		replacement, _ := c.m.Load("key")
		// Resume the real get completion AFTER a new result has been published.
		c.discardExpired("key", observed.(*cacheEntry))
		if stored, _ := c.m.Load("key"); stored != replacement {
			t.Fatal("expired reader deleted fresh replacement")
		}
		if e, state := c.get("key"); state != "fresh" || e.result != fresh {
			t.Fatal("replacement not usable")
		}
		c.m.Store("expired-control", old)
		if e, state := c.get("expired-control"); e != nil || state != "miss" {
			t.Fatal("expired control admitted")
		}
		if _, ok := c.m.Load("expired-control"); ok {
			t.Fatal("get no longer completes expired cleanup")
		}
	})
}

func TestComposeInvalidationAfterPublishBeforeWaiterDelivery(t *testing.T) {
	t.Run("same-scope-replacement", func(t *testing.T) {
		old, fresh := refreshResult(), refreshResult()
		var calls atomic.Int32
		a := publicationAuth(t, func(context.Context) (*ResolveResult, error) { calls.Add(1); return old, nil })
		ctx := &composeContext{Context: context.Background(), at: 7, entered: make(chan struct{}), release: make(chan struct{})}
		var release sync.Once
		defer release.Do(func() { close(ctx.release) })
		done := make(chan composeAuthOutcome, 1)
		go func() { result, err := a.Authenticate(ctx, "k", "s"); done <- composeAuthOutcome{result, err} }()
		composeWait(t, ctx.entered)
		key := CacheKey("k", "s")
		if e, _ := a.cache.peek(key); e == nil || e.result != old {
			t.Fatal("delivery held before worker publication")
		}
		a.cache.putPositive(key, fresh)
		replacement, _ := a.cache.m.Load(key)
		release.Do(func() { close(ctx.release) })
		got := composeOutcome(t, done)
		if got.err != nil || got.result != fresh || calls.Load() != 1 {
			t.Fatalf("same-scope replacement bypassed exact-result retry: %v", got.err)
		}
		if current, _ := a.cache.m.Load(key); current != replacement {
			t.Fatal("old waiter published over replacement")
		}
	})
	for _, churn := range []bool{false, true} {
		t.Run(map[bool]string{false: "one-retry", true: "bounded-churn"}[churn], func(t *testing.T) {
			old, fresh := refreshResult(), refreshResult()
			var calls atomic.Int32
			var a *Authenticator
			a = publicationAuth(t, func(context.Context) (*ResolveResult, error) {
				if calls.Add(1) == 1 {
					return old, nil
				}
				if churn {
					a.InvalidateKey(CacheKey("k", "s"))
				}
				return fresh, nil
			})
			ctx := &composeContext{Context: context.Background(), at: 7, entered: make(chan struct{}), release: make(chan struct{})}
			done := make(chan composeAuthOutcome, 1)
			go func() { r, e := a.Authenticate(ctx, "k", "s"); done <- composeAuthOutcome{r, e} }()
			composeWait(t, ctx.entered)
			if e, _ := a.cache.peek(CacheKey("k", "s")); e == nil || e.result != old {
				close(ctx.release)
				t.Fatal("flight was not published before delivery barrier")
			}
			a.InvalidateKey(CacheKey("k", "s"))
			close(ctx.release)
			got := composeOutcome(t, done)
			if calls.Load() != 2 || got.result == old {
				t.Fatal("stale admission or wrong retry count")
			}
			if churn {
				var changed *ProjectAuthChangedError
				if got.result != nil || !errors.As(got.err, &changed) {
					t.Fatalf("churn admitted: %v", got.err)
				}
			} else if got.result != fresh || got.err != nil {
				t.Fatalf("fresh retry failed: %v", got.err)
			}
		})
	}
}
