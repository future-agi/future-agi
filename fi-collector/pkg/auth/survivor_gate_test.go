package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestSurvivorUnionCacheGateCancellation(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, path := range []string{"fresh-hit", "miss-stamp", "delivered-flight"} {
			for _, ending := range []string{"cancel", "deadline"} {
				t.Run(kind+"/"+path+"/"+ending, func(t *testing.T) {
					api, token, candidate := survivorCredential(t, kind)
					var calls atomic.Int32
					resolve := publicationFunc(func(context.Context) (*ResolveResult, error) {
						calls.Add(1)
						return candidate, nil
					})
					r := survivorGrantResolver{publicationFunc: resolve, grant: func(ctx context.Context, _ *GrantClaims) (*ResolveResult, error) {
						return resolve(ctx)
					}}
					a, err := NewWithResolver(Config{Peppers: mustPeppers(t, goldenPepper)}, r, nil, nil)
					if err != nil {
						t.Fatal(err)
					}
					key := CacheKey(api, token)
					var entry *cacheEntry
					if path == "fresh-hit" {
						entry = survivorPublish(t, a.cache, key, candidate)
					}
					base, cancel := context.WithCancel(context.Background())
					if ending == "deadline" {
						cancel()
						base, cancel = context.WithTimeout(context.Background(), time.Second)
					}
					defer cancel()
					deadline, hasDeadline := base.Deadline()
					gateReached := make(chan struct{})
					delivered := newSurvivorBarrier()
					defer delivered.open()
					gateDoneCall := int32(1)
					if path == "delivered-flight" {
						gateDoneCall = 3 // Stamp select, flight select, admission select.
					}
					probe := &survivorContextProbe{Context: base, onDone: func(n int32) {
						if n == gateDoneCall {
							close(gateReached)
						}
					}}
					if path == "delivered-flight" {
						// Err #7 is credentialDeadline AFTER the DoChan receive.
						// The worker has published and delivered, not merely resolved.
						probe.beforeErr = func(n int32) {
							if n == 7 {
								delivered.stop()
							}
						}
					}
					held := false
					defer func() {
						if held {
							a.cache.mu.Unlock()
						}
					}()
					if path != "delivered-flight" {
						a.cache.mu.Lock()
						held = true
					}
					done := survivorAuthenticate(a, probe, api, token)
					if path == "delivered-flight" {
						survivorWait(t, delivered.entered, "post-delivery deadline check")
						entry, _ = a.cache.peek(key)
						if entry == nil || entry.result != candidate || calls.Load() != 1 {
							t.Fatal("barrier is not after successful worker publication/delivery")
						}
						a.cache.mu.Lock()
						held = true
						delivered.open()
					}
					survivorWait(t, gateReached, "caller cache-gate select")
					if base.Err() != nil {
						t.Fatal("caller expired before reaching the contended gate")
					}
					generation, epoch := a.cache.generation.Load(), a.cache.generationFor(key).epoch
					survivorResident(t, a.cache, key, entry)
					wantErr := context.Canceled
					if ending == "deadline" {
						wantErr = context.DeadlineExceeded
						survivorWait(t, base.Done(), "original request deadline")
					} else {
						cancel()
					}
					got := survivorReceive(t, done) // Gate deliberately still held.
					if got.result != nil || !errors.Is(got.err, wantErr) || base.Err() != wantErr {
						t.Fatalf("lost original cancellation: result=%v err=%v", got.result != nil, got.err)
					}
					if len(a.cache.mu.held) != 1 || a.cache.generation.Load() != generation || a.cache.generationFor(key).epoch != epoch {
						t.Fatal("test must retain the unrelated gate hold and unchanged authority")
					}
					if d, ok := probe.Deadline(); ok != hasDeadline || !d.Equal(deadline) {
						t.Fatal("original request deadline changed")
					}
					wantCalls := int32(0)
					if path == "delivered-flight" {
						wantCalls = 1
					}
					if calls.Load() != wantCalls {
						t.Fatal("cancellation retried or miss stamp reached persistence")
					}
					survivorResident(t, a.cache, key, entry)
					a.cache.mu.Unlock()
					held = false
					select {
					case _, ok := <-done:
						if ok {
							t.Fatal("caller returned a second result after gate release")
						}
					case <-time.After(2 * time.Second):
						t.Fatal("caller did not finish")
					}
					survivorResident(t, a.cache, key, entry)
					live, stop := context.WithTimeout(context.Background(), 2*time.Second)
					defer stop()
					if _, _, err := a.cache.validationStamp(live, key); err != nil {
						t.Fatalf("canceled waiter leaked the cache gate: %v", err)
					}
				})
			}
		}
	}
}

func TestSurvivorUnionCancellationAfterCacheObservation(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		t.Run(kind, func(t *testing.T) {
			api, token, candidate := survivorCredential(t, kind)
			a := publicationAuth(t, func(context.Context) (*ResolveResult, error) {
				return nil, errors.New("fresh observation must not query")
			})
			a.cfg.Peppers = mustPeppers(t, goldenPepper)
			key := CacheKey(api, token)
			entry := survivorPublish(t, a.cache, key, candidate)
			base, cancel := context.WithCancel(context.Background())
			defer cancel()
			observed := newSurvivorBarrier()
			defer observed.open()
			probe := &survivorContextProbe{Context: base, beforeErr: func(n int32) {
				if n == 5 { // currentResult has observed the live pointer and unlocked.
					observed.stop()
				}
			}}
			done := survivorAuthenticate(a, probe, api, token)
			survivorWait(t, observed.entered, "post-observation context check")
			if len(a.cache.mu.held) != 0 || base.Err() != nil {
				t.Fatal("observation did not finish before cancellation")
			}
			survivorResident(t, a.cache, key, entry)
			cancel()
			observed.open()
			got := survivorReceive(t, done)
			if got.result != nil || !errors.Is(got.err, context.Canceled) {
				t.Fatalf("post-observation cancellation admitted: %v", got.err)
			}
			survivorResident(t, a.cache, key, entry)
		})
	}
}

func TestSurvivorUnionReadyGateCancellationDoesNotLeak(t *testing.T) {
	for _, when := range []string{"already-canceled", "after-acquire"} {
		t.Run(when, func(t *testing.T) {
			c := newCache(30*time.Second, 0)
			candidate := refreshResult()
			entry := survivorPublish(t, c, "key", candidate)
			base, cancel := context.WithCancel(context.Background())
			defer cancel()
			probe := &survivorContextProbe{Context: base}
			observedAcquired := false
			if when == "already-canceled" {
				cancel()
			} else {
				probe.beforeErr = func(n int32) {
					if n == 2 { // Token acquired; exercise LockContext's second check.
						observedAcquired = len(c.mu.held) == 1
						cancel()
					}
				}
			}
			ok, err := c.currentResult(probe, "key", candidate)
			if ok || !errors.Is(err, context.Canceled) || len(c.mu.held) != 0 {
				t.Fatalf("ready-token cancellation leaked success or gate: %v", err)
			}
			if when == "after-acquire" && !observedAcquired {
				t.Fatal("cancellation moved past acquisition to an already-unlocked observation")
			}
			survivorResident(t, c, "key", entry)
			live, stop := context.WithTimeout(context.Background(), 2*time.Second)
			defer stop()
			if ok, err := c.currentResult(live, "key", candidate); !ok || err != nil {
				t.Fatalf("subsequent live admission lost its token: %v", err)
			}
		})
	}
}
