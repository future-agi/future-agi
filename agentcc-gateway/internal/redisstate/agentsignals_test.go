package redisstate

import (
	"context"
	"fmt"
	"math"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
	"github.com/futureagi/agentcc-gateway/internal/routing/outcomes"
)

// signalTestStore is an AgentSignalStore on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:agentsignals:<test>:<nonce>:, whose keys are
// deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips without
// Redis.
func signalTestStore(t *testing.T) (*AgentSignalStore, redis.UniversalClient, string) {
	t.Helper()
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := NewClient(Config{Address: redisAddr(), DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", redisAddr(), err)
	}
	prefix := fmt.Sprintf("test:agentsignals:%s:%d:", t.Name(), time.Now().UnixNano())
	rdb := client.Redis()
	t.Cleanup(func() {
		ctx := context.Background()
		iter := rdb.Scan(ctx, 0, prefix+"*", 1000).Iterator()
		var keys []string
		for iter.Next(ctx) {
			keys = append(keys, iter.Val())
		}
		if len(keys) > 0 {
			rdb.Del(ctx, keys...)
		}
		client.Close()
	})
	return NewAgentSignalStore(client, prefix), rdb, prefix
}

type signalClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *signalClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *signalClock) advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

func newSignalClock() *signalClock {
	return &signalClock{t: time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC)}
}

func TestAgentSignalsAffinityRoundTripAndTTLExpiry(t *testing.T) {
	store, rdb, prefix := signalTestStore(t)
	ctx := context.Background()
	aff := affinity.NewStore(store, 10, nil)
	pin := affinity.Value{Model: "vendor/m", Provider: "openrouter", RequestID: "req-1", PrefixTokens: 20000}
	aff.Set(ctx, "org-1", "run:r1", affinity.ClassMain, pin, 300*time.Millisecond)

	key := prefix + affinity.Key("org-1", "run:r1", affinity.ClassMain)
	if pttl := rdb.PTTL(ctx, key).Val(); pttl <= 0 || pttl > 300*time.Millisecond {
		t.Fatalf("PTTL = %v, want within (0, 300ms]", pttl)
	}
	if got, ok := aff.Get(ctx, "org-1", "run:r1", affinity.ClassMain); !ok || got != pin {
		t.Fatalf("Get = %+v, %v; want %+v", got, ok, pin)
	}
	time.Sleep(450 * time.Millisecond)
	if v, ok := aff.Get(ctx, "org-1", "run:r1", affinity.ClassMain); ok {
		t.Fatalf("the pin outlived its TTL: %+v", v)
	}
	aff.Set(ctx, "org-1", "s1", affinity.ClassVerify, pin, time.Minute)
	aff.Break(ctx, "org-1", "s1", affinity.ClassVerify)
	if n := rdb.Exists(ctx, prefix+affinity.Key("org-1", "s1", affinity.ClassVerify)).Val(); n != 0 {
		t.Fatal("Break left the pin in Redis")
	}
}

func TestAgentSignalsAffinityLRUFallbackWhenRedisDown(t *testing.T) {
	client := &Client{breaker: newCircuitBreaker(1, time.Hour)}
	client.breaker.recordFailure() // open: every call fails at once
	store := NewAgentSignalStore(client, "test:agentsignals:down:")
	ctx := context.Background()
	if _, _, err := store.GetAffinity(ctx, "k"); err == nil {
		t.Fatal("an open breaker did not fail the call")
	}
	aff := affinity.NewStore(store, 10, nil)
	pin := affinity.Value{Model: "vendor/m", RequestID: "req-1"}
	aff.Set(ctx, "org-1", "s1", affinity.ClassMain, pin, time.Hour)
	if got, ok := aff.Get(ctx, "org-1", "s1", affinity.ClassMain); !ok || got != pin {
		t.Fatalf("with Redis down: Get = %+v, %v; want the in-memory pin", got, ok)
	}
	out := outcomes.NewStore(store, 10, nil)
	out.Remember(ctx, "org-1", "req-1", outcomes.Last{Model: "vendor/m"})
	if _, ok := out.Last(ctx, "org-1", "req-1"); !ok {
		t.Fatal("with Redis down the Last record was lost")
	}
	if NewAgentSignalStore(nil, "").SetPrev(ctx, "k", "r", time.Second) == nil {
		t.Fatal("a store without a client reported success")
	}
}

func TestAgentSignalsOutcomeDecayHalvesAfterHalfLife(t *testing.T) {
	store, rdb, prefix := signalTestStore(t)
	ctx := context.Background()
	clock := newSignalClock()
	out := outcomes.NewStore(store, 10, clock.now)
	k := outcomes.Key{Org: "org-1", Agent: "researcher", Step: models.StepAct, Model: "vendor/m"}
	hl := outcomes.HalfLife(7200)
	for range 4 {
		out.Record(ctx, k, outcomes.ClassOK.Outcome(100), hl)
	}
	out.Record(ctx, k, outcomes.Outcome{ErrorClass: models.ErrClassUpstream5xx}, hl)
	out.Record(ctx, k, outcomes.Outcome{ErrorClass: models.ErrClassUpstream5xx}, hl)
	clock.advance(hl)
	if c := out.Counters(ctx, k, hl); !near(c.N, 3) || !near(c.Errors, 1) {
		t.Fatalf("read after one half-life: N %v, errors %v; want 3 and 1", c.N, c.Errors)
	}
	// A write after the half-life decays the stored counts first.
	out.Record(ctx, k, outcomes.ClassEmpty.Outcome(100), hl)
	h := rdb.HGetAll(ctx, prefix+k.String()).Val()
	for field, want := range map[string]float64{outcomes.FieldN: 4, outcomes.FieldErrors: 1, outcomes.FieldEmpty: 1,
		outcomes.LatencyField(outcomes.LatencyBin(100)): 3} {
		got, _ := strconv.ParseFloat(h[field], 64)
		if !near(got, want) {
			t.Errorf("hash field %s = %v, want %v (hash %v)", field, got, want, h)
		}
	}
	if h[outcomes.FieldTsMs] != strconv.FormatInt(clock.now().UnixMilli(), 10) {
		t.Errorf("ts_ms = %s, want %d", h[outcomes.FieldTsMs], clock.now().UnixMilli())
	}
	// Another node whose clock runs behind neither decays nor rewinds.
	behind := outcomes.NewStore(store, 10, func() time.Time { return clock.now().Add(-time.Hour) })
	behind.Record(ctx, k, outcomes.ClassOK.Outcome(100), hl)
	if n, _ := strconv.ParseFloat(rdb.HGet(ctx, prefix+k.String(), outcomes.FieldN).Val(), 64); !near(n, 5) {
		t.Errorf("n after a write from behind = %v, want 5", n)
	}
}

func TestAgentSignalsOutcomeTTLsAndJudgement(t *testing.T) {
	store, rdb, prefix := signalTestStore(t)
	ctx := context.Background()
	out := outcomes.NewStore(store, 10, newSignalClock().now) // stopped: no decay between writes
	hl := outcomes.HalfLife(86400)
	last := outcomes.Last{Agent: "researcher", Run: "R2", Step: models.StepAct, Model: "vendor/m", Class: outcomes.ClassOK}
	k := last.Key("org-1")
	out.Record(ctx, k, outcomes.ClassOK.Outcome(80), hl)
	out.Remember(ctx, "org-1", "req-1", last)
	out.SetPrev(ctx, "org-1", "s1", affinity.ClassMain, "req-1")

	for key, want := range map[string]time.Duration{
		k.String():                         outcomes.CountersTTL,
		outcomes.LastKey("org-1", "req-1"): outcomes.LastTTL,
		outcomes.PrevKey("org-1", "s1", affinity.ClassMain): outcomes.LastTTL,
	} {
		if pttl := rdb.PTTL(ctx, prefix+key).Val(); pttl <= want-time.Minute || pttl > want {
			t.Errorf("PTTL(%s) = %v, want about %v", key, pttl, want)
		}
	}
	// The per-request Last record, and the pointer to it,
	// live at most an hour in Redis.
	for _, key := range []string{outcomes.LastKey("org-1", "req-1"), outcomes.PrevKey("org-1", "s1", affinity.ClassMain)} {
		if pttl := rdb.PTTL(ctx, prefix+key).Val(); pttl <= 0 || pttl > time.Hour {
			t.Errorf("PTTL(%s) = %v, want within (0, 1h]", key, pttl)
		}
	}
	if got, ok := out.Last(ctx, "org-1", "req-1"); !ok || got != last {
		t.Fatalf("Last = %+v, %v; want %+v", got, ok, last)
	}
	if id, ok := out.Prev(ctx, "org-1", "s1", affinity.ClassMain); !ok || id != "req-1" {
		t.Fatalf("Prev = %q, %v", id, ok)
	}
	if !out.Judge(ctx, "org-1", "req-1", last, outcomes.VerdictVerifyFailed, hl) {
		t.Fatal("the first verdict was not counted")
	}
	if out.Judge(ctx, "org-1", "req-1", last, outcomes.VerdictVerifyFailed, hl) {
		t.Fatal("a second verdict for the generation was counted")
	}
	if v := rdb.HGet(ctx, prefix+k.String(), outcomes.FieldVerifyFailed).Val(); v != "1" {
		t.Errorf("hash field verify_failed = %q, want 1", v)
	}
	if got, _ := out.Last(ctx, "org-1", "req-1"); !got.Judged {
		t.Error("the Last record is not marked judged")
	}
	// A claim on a generation whose Last record has expired creates nothing.
	if claimed, found, err := store.ClaimJudgement(ctx, outcomes.LastKey("org-1", "gone")); err != nil || claimed || found {
		t.Errorf("ClaimJudgement(gone) = %v, %v, %v", claimed, found, err)
	}
	if rdb.Exists(ctx, prefix+outcomes.LastKey("org-1", "gone")).Val() != 0 {
		t.Error("a claim on a missing record created a key")
	}
}

func TestAgentSignalsMissesKeepTheBreakerClosed(t *testing.T) {
	store, _, _ := signalTestStore(t)
	ctx := context.Background()
	for i := range 10 {
		if _, ok, err := store.GetPrev(ctx, "missing:"+strconv.Itoa(i)); ok || err != nil {
			t.Fatalf("GetPrev(missing) = %v, %v", ok, err)
		}
		if _, ok, err := store.GetLast(ctx, "missing:"+strconv.Itoa(i)); ok || err != nil {
			t.Fatalf("GetLast(missing) = %v, %v", ok, err)
		}
	}
	if !store.client.Available() {
		t.Fatal("misses opened the Redis circuit breaker")
	}
}

// The plugin and /v1/feedback call the store on the request's context, which
// has often expired by then. Those calls must still reach Redis, and must
// never count against the circuit breaker every Redis store shares.
func TestAgentSignalsExpiredCallerContextKeepsBreakerClosed(t *testing.T) {
	store, rdb, prefix := signalTestStore(t)
	expired, cancelExpired := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancelExpired()
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	out := outcomes.NewStore(store, 10, newSignalClock().now)
	aff := affinity.NewStore(store, 10, nil)
	hl := outcomes.HalfLife(86400)
	for name, ctx := range map[string]context.Context{"expired": expired, "cancelled": cancelled} {
		org := "org-" + name
		last := outcomes.Last{Agent: "researcher", Step: models.StepAct, Model: "vendor/m", Class: outcomes.ClassOK}
		// Three timed-out upstream calls, as the plugin records them.
		for range 3 {
			out.Record(ctx, last.Key(org), outcomes.Outcome{ErrorClass: models.ErrClassTimeout}, hl)
		}
		out.Remember(ctx, org, "req-1", last)
		out.SetPrev(ctx, org, "s1", affinity.ClassMain, "req-1")
		if id, ok := out.Prev(ctx, org, "s1", affinity.ClassMain); !ok || id != "req-1" {
			t.Errorf("%s: Prev = %q, %v", name, id, ok)
		}
		if _, ok := out.Last(ctx, org, "req-1"); !ok {
			t.Errorf("%s: Last missed", name)
		}
		if !out.Judge(ctx, org, "req-1", last, outcomes.VerdictVerifyFailed, hl) {
			t.Errorf("%s: the verdict was not counted", name)
		}
		aff.Set(ctx, org, "s1", affinity.ClassMain, affinity.Value{Model: "vendor/m", RequestID: "req-1"}, time.Minute)
		if _, ok := aff.Get(ctx, org, "s1", affinity.ClassMain); !ok {
			t.Errorf("%s: the pin was not read back", name)
		}
		// Everything reached Redis, not the in-memory fallback.
		h := rdb.HGetAll(context.Background(), prefix+last.Key(org).String()).Val()
		if h[outcomes.FieldErrors] != "3" || h[outcomes.FieldVerifyFailed] != "1" {
			t.Errorf("%s: counters hash = %v, want errors 3 and verify_failed 1", name, h)
		}
		for _, key := range []string{outcomes.LastKey(org, "req-1"), outcomes.PrevKey(org, "s1", affinity.ClassMain),
			affinity.Key(org, "s1", affinity.ClassMain)} {
			if rdb.Exists(context.Background(), prefix+key).Val() != 1 {
				t.Errorf("%s: %s is not in Redis", name, key)
			}
		}
		aff.Break(ctx, org, "s1", affinity.ClassMain)
		if rdb.Exists(context.Background(), prefix+affinity.Key(org, "s1", affinity.ClassMain)).Val() != 0 {
			t.Errorf("%s: Break left the pin in Redis", name)
		}
	}
	if !store.client.Available() {
		t.Fatal("calls on an expired or cancelled request context opened the shared Redis circuit breaker")
	}
}

func near(got, want float64) bool { return math.Abs(got-want) < 1e-6 }
