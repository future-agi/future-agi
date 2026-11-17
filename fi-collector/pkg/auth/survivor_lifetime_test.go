package auth

import (
	"context"
	"errors"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

func survivorExpiryBoundary(t *testing.T, expiry int64) {
	t.Helper()
	timer := time.NewTimer(time.Until(time.Unix(expiry, 0)))
	defer timer.Stop()
	select {
	case <-timer.C:
	case <-time.After(2 * time.Second):
		t.Fatal("grant expiry boundary did not arrive")
	}
	if time.Now().Unix() < expiry {
		t.Fatal("timer fired before the exact grant expiry")
	}
}

func TestSurvivorUnionAgedQueryCannotReturn(t *testing.T) {
	const ttl = 25 * time.Millisecond
	var calls atomic.Int32
	r := publicationFunc(func(ctx context.Context) (*ResolveResult, error) {
		calls.Add(1)
		// The production stamp predates entry into persistence. Holding for one
		// whole configured TTL here must age it out, even on the bounded retry.
		timer := time.NewTimer(ttl)
		defer timer.Stop()
		select {
		case <-timer.C:
			return refreshResult(), nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	})
	a, err := NewWithResolver(Config{CacheTTL: ttl}, r, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	got := survivorReceive(t, survivorAuthenticate(a, ctx, "key", "secret"))
	var changed *ProjectAuthChangedError
	if got.result != nil || !errors.As(got.err, &changed) || calls.Load() != 2 {
		t.Fatalf("aged query returned authority or lost retry bound: calls=%d err=%v", calls.Load(), got.err)
	}
	survivorResident(t, a.cache, CacheKey("key", "secret"), nil)
}

func TestSurvivorUnionGrantExpiryAcrossFlight(t *testing.T) {
	for _, boundary := range []string{"primary-result", "delivered-result"} {
		t.Run(boundary, func(t *testing.T) {
			now := time.Now()
			fields := sandboxFields(now)
			fields[8] = strconv.FormatInt(now.Add(1500*time.Millisecond).Unix(), 10)
			api, token := "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
			peppers := mustPeppers(t, goldenPepper)
			claims, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, now)
			if err != nil {
				t.Fatal(err)
			}
			held := newSurvivorBarrier()
			defer held.open()
			var calls atomic.Int32
			candidate := refreshResult()
			candidate.Grant = claims
			r := survivorGrantResolver{
				publicationFunc: func(context.Context) (*ResolveResult, error) {
					return nil, errors.New("grant used personal validation")
				},
				grant: func(context.Context, *GrantClaims) (*ResolveResult, error) {
					calls.Add(1)
					if boundary == "primary-result" {
						held.stop()
					}
					return candidate, nil
				},
			}
			a, err := NewWithResolver(Config{Peppers: peppers}, r, nil, nil)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			probe := &survivorContextProbe{Context: ctx, beforeErr: func(n int32) {
				if boundary == "delivered-result" && n == 7 {
					held.stop() // After actual worker publication and DoChan delivery.
				}
			}}
			done := survivorAuthenticate(a, probe, api, token)
			survivorWait(t, held.entered, boundary)
			if time.Now().Unix() >= claims.Exp || calls.Load() != 1 {
				t.Fatal("expiry must occur after the intended live-grant boundary")
			}
			key := CacheKey(api, token)
			if boundary == "primary-result" {
				survivorResident(t, a.cache, key, nil)
			} else if e, status := a.cache.peek(key); status != "fresh" || e.result != candidate {
				t.Fatal("delivered case did not publish before the expiry barrier")
			}
			survivorExpiryBoundary(t, claims.Exp)
			held.open()
			got := survivorReceive(t, done)
			if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) || calls.Load() != 1 {
				t.Fatalf("expired grant admitted or retried: calls=%d err=%v", calls.Load(), got.err)
			}
			// Peek first: an expired physically resident delivery is never fresh.
			if e, status := a.cache.peek(key); e != nil || status != "miss" {
				t.Fatal("expired flight retained fresh authority")
			}
			if e, status := a.cache.get(key); e != nil || status != "miss" {
				t.Fatal("expired flight remained admissible")
			}
			survivorResident(t, a.cache, key, nil)
		})
	}
}
