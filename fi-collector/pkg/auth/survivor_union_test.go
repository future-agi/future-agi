package auth

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// These regressions exercise the composed cache, not either parent's warm cache.
func TestSurvivorUnionFreshOnlyAndExpiry(t *testing.T) {
	c := newCache(time.Hour, time.Hour)
	for _, age := range []time.Duration{30 * time.Second, time.Minute} {
		c.m.Store("key", &cacheEntry{result: refreshResult(), fetchedAt: time.Now().Add(-age)})
		if entry, status := c.get("key"); entry != nil || status != "miss" {
			t.Fatal("served authority at or beyond the thirty-second bound")
		}
	}
	expired := refreshResult()
	expired.Grant = &GrantClaims{Exp: time.Now().Unix()}
	c.m.Store("key", &cacheEntry{result: expired, fetchedAt: time.Now()})
	if entry, status := c.get("key"); entry != nil || status != "miss" {
		t.Fatal("served expired grant from fresh cache")
	}
}

func TestSurvivorUnionGenerationRefusesLatePublication(t *testing.T) {
	for _, notice := range []string{"pair", "digest", "grant", "flush"} {
		t.Run(notice, func(t *testing.T) {
			c := newCache(30*time.Second, 0)
			stamp, epoch, err := c.validationStamp(context.Background(), "key")
			if err != nil {
				t.Fatal(err)
			}
			switch notice {
			case "pair":
				c.invalidateKey("key")
			case "digest":
				c.evictAPIKey(APIKeyDigest("uncached"))
			case "grant":
				c.evictGrant("uncached")
			case "flush":
				c.flush()
			}
			ok, err := c.publishValidated(context.Background(), "key", refreshResult(), epoch, stamp)
			if err != nil || ok || c.generation.Load() <= stamp.generation {
				t.Fatalf("late publication survived %s: published=%v err=%v", notice, ok, err)
			}
			if _, status := c.peek("key"); status != "miss" {
				t.Fatal("late writer repopulated invalidated authority")
			}
		})
	}
}

type survivorCachedResolver struct {
	publicationFunc
	recheck func(context.Context, *ResolveResult) (bool, error)
}

func (r survivorCachedResolver) RevalidateCached(ctx context.Context, _, _ string, prior *ResolveResult) (bool, error) {
	return r.recheck(ctx, prior)
}

func TestSurvivorUnionCachedRevalidationFences(t *testing.T) {
	for _, mode := range []string{"revoked", "cancel", "invalidation", "outage"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var a *Authenticator
			checked := false
			outage := errors.New("primary unavailable")
			r := survivorCachedResolver{
				publicationFunc: func(context.Context) (*ResolveResult, error) { return nil, nil },
				recheck: func(context.Context, *ResolveResult) (bool, error) {
					checked = true
					switch mode {
					case "revoked":
						return false, nil
					case "cancel":
						cancel()
					case "invalidation":
						a.InvalidateKey(CacheKey("key", "secret"))
					case "outage":
						return false, outage
					}
					return true, nil
				},
			}
			var err error
			a, err = NewWithResolver(Config{}, r, nil, slog.Default())
			if err != nil {
				t.Fatal(err)
			}
			a.cache.putPositive(CacheKey("key", "secret"), refreshResult())
			result, err := a.Authenticate(ctx, "key", "secret")
			if !checked || result != nil || err == nil {
				t.Fatalf("fresh hit bypassed live/post-flight fence: checked=%v result=%v err=%v", checked, result != nil, err)
			}
			if mode == "cancel" && !errors.Is(err, context.Canceled) {
				t.Fatalf("lost original cancellation: %v", err)
			}
			if mode == "outage" && !errors.Is(err, outage) {
				t.Fatalf("primary outage was not preserved: %v", err)
			}
		})
	}
}

func TestSurvivorUnionCancellationDuringColdFlight(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	defer once.Do(func() { close(release) })
	a := publicationAuth(t, func(ctx context.Context) (*ResolveResult, error) {
		close(entered)
		select {
		case <-release:
			return refreshResult(), nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		r, err := a.Authenticate(ctx, "key", "secret")
		if r != nil {
			err = errors.New("canceled caller admitted")
		}
		done <- err
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("lookup did not enter")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancellation lost: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("canceled caller waited for detached worker")
	}
}

func TestSurvivorUnionRevokedGrantRefusedWithFreshCache(t *testing.T) {
	primary, replica := routingDatabase(t), routingDatabase(t)
	fields := sandboxFields(time.Now())
	api, token := "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
	peppers := mustPeppers(t, goldenPepper)
	claims, err := VerifyCollectorGrant(api, token, peppers, []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	for _, pool := range []*pgxpool.Pool{primary, replica} {
		seedEligibility(t, pool, eligibilityFixture{claims.Org, claims.Workspace, claims.User, "synthetic-key", "synthetic-secret"})
		seedGrant(t, pool, claims)
	}
	a, err := New(context.Background(), Config{PGWrite: primary.Config().ConnString(), PGRead: replica.Config().ConnString(), Peppers: peppers}, nil, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if result, err := a.Authenticate(context.Background(), api, token); result == nil || err != nil {
		t.Fatalf("positive control: %v", err)
	}
	routingFixture{t, primary}.exec(`UPDATE accounts_service_grant SET revoked_at=now()`)
	if _, status := a.cache.peek(CacheKey(api, token)); status != "fresh" {
		t.Fatal("test requires a genuinely fresh cached grant")
	}
	if result, err := a.Authenticate(context.Background(), api, token); result != nil || !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("revoked grant admitted from fresh cache: %v", err)
	}
	stale := &PGResolver{read: replica, write: replica}
	if result, err := stale.ValidateGrant(context.Background(), claims); result == nil || err != nil {
		t.Fatalf("replica negative control must remain eligible: %v", err)
	}
}
