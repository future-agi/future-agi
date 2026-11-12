package auth

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
	"google.golang.org/grpc/metadata"
)

func TestComposeSameJTIDifferentTokensDoNotShareFlight(t *testing.T) {
	api, token, result, cfg := composeCredential(t, true)
	fields := sandboxFields(time.Unix(result.Grant.Iat, 0))
	fields[13] = "55555555-5555-4555-8555-555555555555"
	other := signedGrantFields(fields, goldenPepper)
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	defer once.Do(func() { close(release) })
	var calls atomic.Int32
	r := composeResolver{publicationFunc: func(context.Context) (*ResolveResult, error) {
		t.Error("grant fell back to personal auth")
		return nil, nil
	}, grant: func(ctx context.Context, c *GrantClaims) (*ResolveResult, error) {
		calls.Add(1)
		if !sameGrant(c, result.Grant) {
			return nil, nil
		}
		close(entered)
		select {
		case <-release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
		return result, nil
	}}
	a, err := NewWithResolver(cfg, r, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan composeAuthOutcome, 1)
	go func() { got, e := a.Authenticate(context.Background(), api, token); done <- composeAuthOutcome{got, e} }()
	composeWait(t, entered)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	got, e := a.Authenticate(ctx, api, other)
	if got != nil || !errors.Is(e, ErrUnauthenticated) || calls.Load() != 2 {
		t.Fatalf("different token shared persistence: calls=%d error=%v", calls.Load(), e)
	}
	once.Do(func() { close(release) })
	first := composeOutcome(t, done)
	if first.err != nil || first.result != result {
		t.Fatalf("matching registry token refused: %v", first.err)
	}
	for _, bad := range []string{token + "x", other} {
		md := metadata.Pairs("x-api-key", api, "x-secret-key", bad)
		if a.KnownGRPCCredentials(md) {
			t.Fatal("advisory classifier accepted malformed or different claims")
		}
		if got, e := a.Authenticate(context.Background(), api, bad); got != nil || !errors.Is(e, ErrUnauthenticated) {
			t.Fatalf("invalid token bypassed actual authentication: %v", e)
		}
	}
	if !a.KnownGRPCCredentials(metadata.Pairs("x-api-key", api, "x-secret-key", token)) {
		t.Fatal("live exact token not recognized")
	}
}

func TestComposeCanceledLeaderDoesNotCancelLiveFollower(t *testing.T) {
	t.Run("admission-gates", func(t *testing.T) {
		composeCacheGateCancellation(t, []string{"hit", "delivered-flight"})
	})
	for _, grant := range []bool{false, true} {
		t.Run(map[bool]string{false: "personal", true: "grant"}[grant], func(t *testing.T) {
			api, token, result, cfg := composeCredential(t, grant)
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			var calls atomic.Int32
			lookup := func(ctx context.Context) (*ResolveResult, error) {
				calls.Add(1)
				close(entered)
				select {
				case <-release:
				case <-ctx.Done():
					return nil, ctx.Err()
				}
				return result, nil
			}
			r := composeResolver{publicationFunc: lookup, grant: func(ctx context.Context, _ *GrantClaims) (*ResolveResult, error) { return lookup(ctx) }}
			a, e := NewWithResolver(cfg, r, nil, nil)
			if e != nil {
				t.Fatal(e)
			}
			leader, cancel := context.WithCancel(context.Background())
			defer cancel()
			first, last := make(chan composeAuthOutcome, 1), make(chan composeAuthOutcome, 1)
			go func() { got, err := a.Authenticate(leader, api, token); first <- composeAuthOutcome{got, err} }()
			composeWait(t, entered)
			follower := &composeContext{Context: context.Background(), waiting: make(chan struct{})}
			follower.gate.Store(true)
			// Two cache gates also consult Done. Stop immediately before
			// DoChan, then arm the Done observation for the flight wait only.
			follower.gate.Store(false)
			follower.at = 6
			follower.entered = make(chan struct{})
			follower.release = make(chan struct{})
			go func() { got, err := a.Authenticate(follower, api, token); last <- composeAuthOutcome{got, err} }()
			composeWait(t, follower.entered)
			follower.gate.Store(true)
			close(follower.release)
			composeWait(t, follower.waiting)
			cancel()
			out := composeOutcome(t, first)
			if out.result != nil || !errors.Is(out.err, context.Canceled) {
				t.Fatalf("leader cancellation lost: %v", out.err)
			}
			if e, _ := a.cache.peek(CacheKey(api, token)); e != nil {
				t.Fatal("waiter published before worker release")
			}
			if calls.Load() != 1 {
				t.Fatalf("identical token/generation did not coalesce: %d", calls.Load())
			}
			once.Do(func() { close(release) })
			out = composeOutcome(t, last)
			if out.err != nil || out.result != result {
				t.Fatalf("leader canceled follower: %v", out.err)
			}
		})
	}
}

func TestComposeExpiredSeedAndProductionPublicationContracts(t *testing.T) {
	composeExpiredSeedAndProductionPublicationContracts(t)
}

func TestComposeGrantExpiresDuringFlightAndBeforeDelivery(t *testing.T) {
	composeGrantExpiresDuringFlightAndBeforeDelivery(t)
}

func TestComposeGrantRedisDisconnectFencesHeldFlight(t *testing.T) {
	for _, change := range []string{"revoke", "cancel"} {
		t.Run(change, func(t *testing.T) {
			api, token, signed, cfg := composeCredential(t, true)
			claims := signed.Grant
			pool := routingDatabase(t)
			seedEligibility(t, pool, eligibilityFixture{claims.Org, claims.Workspace, claims.User, "synthetic-key", "synthetic-secret"})
			seedGrant(t, pool, claims)
			pg := &PGResolver{write: pool, read: pool}
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			var calls atomic.Int32
			resolver := composeResolver{publicationFunc: func(context.Context) (*ResolveResult, error) { return nil, errors.New("unexpected personal lookup") }, grant: func(ctx context.Context, c *GrantClaims) (*ResolveResult, error) {
				result, err := pg.ValidateGrant(ctx, c)
				if calls.Add(1) == 1 {
					if err != nil || result == nil {
						t.Error("primary did not capture an eligible grant")
					}
					close(entered)
					select {
					case <-release:
					case <-ctx.Done():
						return nil, ctx.Err()
					}
				}
				return result, err
			}}
			mr := miniredis.RunT(t)
			rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
			defer rdb.Close()
			a, err := NewWithResolver(cfg, resolver, rdb, nil)
			if err != nil {
				t.Fatal(err)
			}
			watchCtx, stop := context.WithCancel(context.Background())
			watchDone := make(chan struct{})
			go func() { a.WatchRevocations(watchCtx); close(watchDone) }()
			defer stop()
			awaitCondition(t, func() bool { return a.cache.generation.Load() > 0 })
			// Targeted notices have a separate unrelated-resident survivor control.
			a.cache.putPositive("survivor", refreshResult())
			before := a.cache.generation.Load()
			if err := rdb.Publish(context.Background(), revocationChannel, "grant:unrelated").Err(); err != nil {
				t.Fatal(err)
			}
			awaitCondition(t, func() bool { return a.cache.generation.Load() > before })
			if e, _ := a.cache.peek("survivor"); e == nil {
				t.Fatal("targeted notice flushed unrelated credential")
			}
			old := make(chan composeAuthOutcome, 1)
			go func() { r, e := a.Authenticate(context.Background(), api, token); old <- composeAuthOutcome{r, e} }()
			composeWait(t, entered)
			before = a.cache.generation.Load()
			mr.Close()
			awaitCondition(t, func() bool { return a.cache.generation.Load() > before })
			if e, _ := a.cache.peek("survivor"); e != nil {
				t.Fatal("disconnect did not flush resident credentials")
			}
			if change == "revoke" {
				routingFixture{t, pool}.exec(`UPDATE accounts_service_grant SET revoked_at=now()`)
			} else {
				routingFixture{t, pool}.exec(`UPDATE simulate_hosted_harness_job SET cancel_requested_at=now()`)
			}
			// A marker admitted during the outage must disappear on subscription
			// confirmation, before either post-reconnect request is permitted.
			a.cache.putPositive("outage-marker", refreshResult())
			before = a.cache.generation.Load()
			if err := mr.Restart(); err != nil {
				t.Fatal(err)
			}
			awaitCondition(t, func() bool {
				counts := mr.PubSubNumSub(revocationChannel, projectInvalidateChannel)
				if counts[revocationChannel] == 0 || counts[projectInvalidateChannel] == 0 {
					return false
				}
				e, _ := a.cache.peek("outage-marker")
				return e == nil && a.cache.generation.Load() > before
			})
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			if r, e := a.Authenticate(ctx, api, token); r != nil || !errors.Is(e, ErrUnauthenticated) {
				t.Fatalf("fresh request accepted committed %s: %v", change, e)
			}
			once.Do(func() { close(release) })
			got := composeOutcome(t, old)
			if got.result != nil || !errors.Is(got.err, ErrUnauthenticated) {
				t.Fatalf("held grant refilled after reconnect: %v", got.err)
			}
			if e, _ := a.cache.peek(CacheKey(api, token)); e != nil {
				t.Fatal("old grant returned to cache")
			}
			if calls.Load() != 3 {
				t.Fatalf("held, fresh and bounded-retry primary calls=%d", calls.Load())
			}
			stop()
			composeWait(t, watchDone) // idle Receive must close promptly
		})
	}
}

func TestComposePreQueryAgeBoundsRevocationWithSlowPrimary(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	defer once.Do(func() { close(release) })
	old, fresh := refreshResult(), refreshResult()
	var calls atomic.Int32
	a := publicationAuth(t, func(context.Context) (*ResolveResult, error) {
		if calls.Add(1) == 1 {
			close(entered)
			<-release
			return old, nil
		}
		return fresh, nil
	})
	a.cache.ttl = 20 * time.Millisecond
	done := make(chan composeAuthOutcome, 1)
	go func() { r, e := a.Authenticate(context.Background(), "k", "s"); done <- composeAuthOutcome{r, e} }()
	composeWait(t, entered)
	timer := time.NewTimer(a.cache.ttl)
	defer timer.Stop()
	<-timer.C
	once.Do(func() { close(release) })
	got := composeOutcome(t, done)
	if got.err != nil || got.result != fresh || calls.Load() != 2 {
		t.Fatalf("slow lookup gained completion-time lifetime: calls=%d err=%v", calls.Load(), got.err)
	}
}
