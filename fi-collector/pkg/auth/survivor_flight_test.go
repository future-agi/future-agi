package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestSurvivorUnionSameJTIDifferentTokenFlights(t *testing.T) {
	fields := sandboxFields(time.Now())
	api, token := "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
	otherFields := append([]string(nil), fields...)
	otherFields[13] = "55555555-5555-4555-8555-555555555555"
	otherToken := signedGrantFields(otherFields, goldenPepper)
	peppers := mustPeppers(t, goldenPepper)
	registered, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	other, err := VerifyCollectorGrant(api, otherToken, peppers, []string{"k1"}, time.Now())
	if err != nil || sameGrant(registered, other) || token == otherToken || CacheKey(api, token) != CacheKey(api, otherToken) {
		t.Fatal("control requires valid different signed claims sharing exactly one jti/cache key")
	}
	first := newSurvivorBarrier()
	defer first.open()
	otherEntered := make(chan struct{}, 2)
	var calls atomic.Int32
	candidate := refreshResult()
	candidate.Grant = registered
	r := survivorGrantResolver{
		publicationFunc: func(context.Context) (*ResolveResult, error) {
			return nil, errors.New("grant used personal validation")
		},
		grant: func(_ context.Context, claims *GrantClaims) (*ResolveResult, error) {
			calls.Add(1)
			// Model one registry row bound to the first exact signed claim set.
			if !sameGrant(claims, registered) {
				otherEntered <- struct{}{}
				return nil, nil
			}
			first.stop()
			return candidate, nil
		},
	}
	a, err := NewWithResolver(Config{Peppers: peppers}, r, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	leader := survivorAuthenticate(a, ctx, api, token)
	survivorWait(t, first.entered, "held first token validation")
	second := survivorAuthenticate(a, ctx, api, otherToken)
	survivorWait(t, otherEntered, "independent same-jti token validation")
	got := survivorReceive(t, second)
	if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) || calls.Load() != 2 {
		t.Fatalf("other token reused first token's flight/authority: calls=%d err=%v", calls.Load(), got.err)
	}
	survivorResident(t, a.cache, CacheKey(api, token), nil)
	first.open()
	got = survivorReceive(t, leader)
	if got.result != candidate || got.err != nil || calls.Load() != 2 {
		t.Fatalf("registered token positive control: %v", got.err)
	}
	entry, status := a.cache.peek(CacheKey(api, token))
	if status != "fresh" || entry.result != candidate {
		t.Fatal("registered token was not published")
	}
	// Same jti also cannot bypass exact claims on the now-fresh hit.
	got = survivorReceive(t, survivorAuthenticate(a, ctx, api, otherToken))
	if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) || calls.Load() != 3 {
		t.Fatalf("other token reused fresh first-token authority: calls=%d err=%v", calls.Load(), got.err)
	}
	survivorResident(t, a.cache, CacheKey(api, token), entry)
}

func TestSurvivorUnionIdenticalTokenFlightCoalesces(t *testing.T) {
	for _, kind := range []string{"personal", "grant"} {
		for _, cancelLeader := range []bool{false, true} {
			name := "live-leader"
			if cancelLeader {
				name = "canceled-leader"
			}
			t.Run(kind+"/"+name, func(t *testing.T) {
				api, token, candidate := survivorCredential(t, kind)
				worker := newSurvivorBarrier()
				defer worker.open()
				var calls atomic.Int32
				resolve := publicationFunc(func(ctx context.Context) (*ResolveResult, error) {
					n := calls.Add(1)
					if n == 1 {
						worker.stop()
					}
					if err := ctx.Err(); err != nil {
						return nil, err
					}
					return candidate, nil
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
				leader := survivorAuthenticate(a, ctx, api, token)
				survivorWait(t, worker.entered, "shared worker")
				joined := make(chan struct{})
				followerCtx, stop := context.WithCancel(context.Background())
				defer stop()
				probe := &survivorContextProbe{Context: followerCtx, onDone: func(n int32) {
					if n == 2 { // DoChan has returned, so the follower is registered.
						close(joined)
					}
				}}
				follower := survivorAuthenticate(a, probe, api, token)
				survivorWait(t, joined, "identical-token follower registration")
				if calls.Load() != 1 {
					t.Fatal("identical token/generation started another worker")
				}
				if cancelLeader {
					cancel()
					got := survivorReceive(t, leader)
					if got.result != nil || !errors.Is(got.err, context.Canceled) {
						t.Fatalf("canceled leader waited or admitted: %v", got.err)
					}
					survivorResident(t, a.cache, CacheKey(api, token), nil)
				}
				worker.open()
				got := survivorReceive(t, follower)
				if got.result != candidate || got.err != nil || calls.Load() != 1 {
					t.Fatalf("live follower lost shared worker: calls=%d err=%v", calls.Load(), got.err)
				}
				if !cancelLeader {
					got = survivorReceive(t, leader)
					if got.result != candidate || got.err != nil {
						t.Fatalf("live leader control: %v", got.err)
					}
				}
				entry, status := a.cache.peek(CacheKey(api, token))
				if status != "fresh" || entry.result != candidate {
					t.Fatal("shared worker did not publish its exact result")
				}
			})
		}
	}
}
