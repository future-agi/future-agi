package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func awaitCondition(t *testing.T, fn func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if fn() {
			return
		}
		time.Sleep(time.Millisecond * 5)
	}
	t.Fatal("condition not reached")
}

func startFreshnessWatcher(t *testing.T, a *Authenticator) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); a.WatchRevocations(ctx) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			t.Error("watcher did not stop")
		}
	})
	// Both initial subscription confirmations have been processed. The second
	// one flushes, so subsequent fixtures cannot be evicted by watcher startup.
	awaitCondition(t, func() bool { return a.cache.generation.Load() > 0 })
}

func TestFreshnessGenerationFencesAllEvictions(t *testing.T) {
	for _, action := range []string{"key", "grant", "legacy", "flush"} {
		t.Run(action, func(t *testing.T) {
			c := newCache(time.Minute, time.Hour)
			stamp := c.stamp()
			result := &ResolveResult{APIKeyDigest: "public-id-digest"}
			switch action {
			case "key":
				c.evictAPIKey(result.APIKeyDigest)
			case "grant":
				c.evictGrant("jti")
			case "legacy":
				c.evict("entry")
			case "flush":
				c.flush()
			}
			c.putPositive("entry", result, stamp)
			if _, status := c.get("entry"); status != "miss" {
				t.Fatal("pre-eviction lookup was cached")
			}
			c.putPositive("entry", result, c.stamp())
			if _, status := c.get("entry"); status != "fresh" {
				t.Fatal("post-eviction lookup was not cached")
			}
		})
	}
	c := newCache(time.Minute, time.Hour)
	stamp := c.stamp()
	stamp.started = time.Now().Add(-31 * time.Second)
	c.putPositive("slow-query", &ResolveResult{}, stamp)
	if _, status := c.get("slow-query"); status != "miss" {
		t.Fatal("cache lifetime was extended by query latency")
	}
}

func TestFreshnessRedisReconnectFlushesCredentialEntries(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()
	a := &Authenticator{rdb: rdb, cache: newCache(time.Minute, time.Hour), log: slog.Default()}
	startFreshnessWatcher(t, a)
	a.cache.putPositive("personal", &ResolveResult{})
	a.cache.putPositive("grant:jti", &ResolveResult{})
	if err := rdb.Publish(context.Background(), revocationChannel, "grant:jti").Err(); err != nil {
		t.Fatal(err)
	}
	awaitCondition(t, func() bool { _, s := a.cache.get("grant:jti"); return s == "miss" })
	if _, s := a.cache.get("personal"); s != "fresh" {
		t.Fatal("grant notice evicted unrelated entry")
	}
	before := a.cache.generation.Load()
	mr.Close()
	awaitCondition(t, func() bool { return a.cache.generation.Load() > before })
	if _, s := a.cache.get("personal"); s != "miss" {
		t.Fatal("disconnect did not flush")
	}
	if err := mr.Restart(); err != nil {
		t.Fatal(err)
	}
	// A result admitted during the outage cannot survive a reconnect either.
	a.cache.putPositive("during-cut", &ResolveResult{})
	awaitCondition(t, func() bool { _, s := a.cache.get("during-cut"); return s == "miss" })
}

// blockQueryEnd holds a completed eligibility SELECT after its result was
// obtained, so a committed change and its notice can overtake cache insertion.
type blockQueryEnd struct {
	armed            atomic.Bool
	entered, release chan struct{}
}

func (b *blockQueryEnd) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	return ctx
}
func (b *blockQueryEnd) TraceQueryEnd(_ context.Context, _ *pgx.Conn, _ pgx.TraceQueryEndData) {
	if b.armed.CompareAndSwap(true, false) {
		close(b.entered)
		<-b.release
	}
}

func TestFreshnessLookupOvertakenByRevocation(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	tracer := &blockQueryEnd{entered: make(chan struct{}), release: make(chan struct{})}
	tracer.armed.Store(true)
	cfg := pool.Config().Copy()
	cfg.ConnConfig.Tracer = tracer
	blocked, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer blocked.Close()
	p := mustPeppers(t, goldenPepper)
	a := &Authenticator{pg: &PGResolver{write: blocked, read: pool, peppers: p}, cache: newCache(30*time.Second, 0), log: slog.Default()}
	done := make(chan composeAuthOutcome, 1)
	go func() {
		got, err := a.Authenticate(context.Background(), f.api, f.secret)
		done <- composeAuthOutcome{got, err}
	}()
	select {
	case <-tracer.entered:
	case <-time.After(5 * time.Second):
		close(tracer.release)
		t.Fatal("lookup never reached fence")
	}
	routingFixture{t, pool}.exec(`UPDATE accounts_orgapikey SET enabled=false`)
	a.evictCredential(RevocationPrefix + APIKeyDigest(f.api))
	// A post-notice request must settle without joining the held old flight.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	got, nextErr := a.Authenticate(ctx, f.api, f.secret)
	close(tracer.release)
	if got != nil || !errors.Is(nextErr, ErrUnauthenticated) {
		t.Fatalf("post-notice lookup did not refuse independently: %v", nextErr)
	}
	if outcome := <-done; outcome.result != nil || !errors.Is(outcome.err, ErrUnauthenticated) {
		t.Fatalf("overtaken lookup admitted revoked credentials: %v", outcome.err)
	}
	if _, s := a.cache.get(CacheKey(f.api, f.secret)); s != "miss" {
		t.Fatal("overtaken lookup populated cache")
	}
	if _, err := a.Authenticate(context.Background(), f.api, f.secret); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("next send did not read primary: %v", err)
	}
}

func TestFreshnessExpiredEntryDuringPGOutageIsUnavailable(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	a, err := New(context.Background(), Config{PGWrite: pool.Config().ConnString(), Peppers: mustPeppers(t, goldenPepper)}, nil, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := a.Authenticate(context.Background(), f.api, f.secret); err != nil {
		t.Fatal(err)
	}
	e, _ := a.cache.get(CacheKey(f.api, f.secret))
	e.fetchedAt = time.Now().Add(-30 * time.Second)
	a.Close()
	request := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/traces", nil)
	request.Header.Set("X-Api-Key", f.api)
	request.Header.Set("X-Secret-Key", f.secret)
	response := httptest.NewRecorder()
	a.HTTPMiddleware(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("stale entry reached handler") })).ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("outage status=%d, want 503", response.Code)
	}
}
