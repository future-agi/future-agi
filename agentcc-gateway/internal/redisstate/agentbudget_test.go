package redisstate

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// agentBudgetTestStore is an AgentBudgetStore on the test Redis
// (TEST_REDIS_ADDR, TEST_REDIS_DB) under the prefix test:agentbudget:<test>:<nonce>:,
// whose keys are deleted when the test ends (SCAN and DEL, never FLUSHDB). It
// skips without TEST_REDIS_ADDR: unlike this package's redisAddr, it has no
// default address, so a run that names no Redis writes to none. A
// TEST_REDIS_DB that is not a database
// number fails the test rather than falling back to DB 0.
func agentBudgetTestStore(t *testing.T) (*AgentBudgetStore, redis.UniversalClient, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db := 0
	if v := os.Getenv("TEST_REDIS_DB"); v != "" {
		var err error
		if db, err = strconv.Atoi(v); err != nil || db < 0 {
			t.Fatalf("TEST_REDIS_DB=%q is not a database number", v)
		}
	}
	client, err := NewClient(Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:agentbudget:%s:%d:", t.Name(), time.Now().UnixNano())
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
	return NewAgentBudgetStore(client, prefix), rdb, prefix
}

const (
	abOrg   = "org-budget"
	abScope = "ledger-L1"
	abTTL   = time.Hour
	abHold  = 15 * time.Minute
)

func mustReserve(t *testing.T, s *AgentBudgetStore, scope, id string, micros, limit int64) AgentBudgetState {
	t.Helper()
	st, err := s.Reserve(bg, abOrg, scope, id, micros, limit, abHold, abTTL)
	if err != nil {
		t.Fatalf("reserve %s: %v", id, err)
	}
	return st
}

// Eight members of one run, each holding $0.01 on a $0.05 scope at once:
// exactly five fit, the held total never passes the limit while they run,
// and nothing stays held once each settles or voids.
func TestAgentBudgetConcurrentMembersHoldAtMostTheLimit(t *testing.T) {
	s, rdb, prefix := agentBudgetTestStore(t)
	heldKey := prefix + "abud:{" + abOrg + "}:" + abScope + ":held"

	var maxHeld atomic.Int64
	stop := make(chan struct{})
	var watch sync.WaitGroup
	watch.Add(1)
	go func() { // samples the held counter while the members run
		defer watch.Done()
		for {
			select {
			case <-stop:
				return
			default:
			}
			if v, err := rdb.Get(bg, heldKey).Int64(); err == nil && v > maxHeld.Load() {
				maxHeld.Store(v)
			}
		}
	}()

	var ok, exhausted atomic.Int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			st, err := s.Reserve(bg, abOrg, abScope, fmt.Sprintf("member-%d", i), 10_000, 50_000, abHold, abTTL)
			if err != nil {
				t.Errorf("member %d: %v", i, err)
				return
			}
			if st.HeldMicros > st.LimitMicros {
				t.Errorf("member %d: held %d over the limit %d", i, st.HeldMicros, st.LimitMicros)
			}
			switch st.Status {
			case AgentBudgetOK:
				ok.Add(1)
			case AgentBudgetExhausted:
				exhausted.Add(1)
			default:
				t.Errorf("member %d: status %q", i, st.Status)
			}
		}(i)
	}
	close(start)
	wg.Wait()
	close(stop)
	watch.Wait()

	if ok.Load() != 5 || exhausted.Load() != 3 {
		t.Fatalf("%d held and %d refused, want 5 and 3", ok.Load(), exhausted.Load())
	}
	if maxHeld.Load() > 50_000 {
		t.Fatalf("held reached %d micros, over the $0.05 limit", maxHeld.Load())
	}
	for i := 0; i < 8; i++ {
		if i%2 == 0 {
			_, err := s.Settle(bg, abOrg, abScope, fmt.Sprintf("member-%d", i), 4_000, abTTL)
			if err != nil {
				t.Fatal(err)
			}
		} else if _, err := s.Void(bg, abOrg, abScope, fmt.Sprintf("member-%d", i), abTTL); err != nil {
			t.Fatal(err)
		}
	}
	st, err := s.Peek(bg, abOrg, abScope)
	if err != nil {
		t.Fatal(err)
	}
	if st.HeldMicros != 0 {
		t.Fatalf("held %d after every member finished, want 0", st.HeldMicros)
	}
}

// A repeated hold id reuses its hold: one hold, held once.
func TestAgentBudgetSameHoldIDIsOneHold(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	first := mustReserve(t, s, abScope, "attempt-1", 10_000, 50_000)
	again := mustReserve(t, s, abScope, "attempt-1", 10_000, 50_000)
	if first.Status != AgentBudgetOK || again.Status != AgentBudgetOK {
		t.Fatalf("statuses %q and %q, want ok twice", first.Status, again.Status)
	}
	if again.HeldMicros != 10_000 {
		t.Fatalf("held %d after the same attempt twice, want 10000", again.HeldMicros)
	}
}

// The first limit is kept, a lower one lowers it, a higher one is ignored.
func TestAgentBudgetLowerLimitWins(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	steps := []struct {
		sent, want int64
	}{{50_000, 50_000}, {20_000, 20_000}, {80_000, 20_000}, {AgentBudgetNoLimit, 20_000}, {5_000, 5_000}}
	for i, step := range steps {
		st := mustReserve(t, s, abScope, fmt.Sprintf("call-%d", i), 0, step.sent)
		if st.LimitMicros != step.want {
			t.Fatalf("step %d: sent %d, limit %d, want %d", i, step.sent, st.LimitMicros, step.want)
		}
	}
}

// A scope with no limit admits every hold; one with a spent limit refuses even
// a hold of nothing.
func TestAgentBudgetExhaustedScope(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	if st := mustReserve(t, s, "no-limit", "a", 1_000_000_000, AgentBudgetNoLimit); st.Status != AgentBudgetOK || st.HasLimit() {
		t.Fatalf("a scope with no limit: %+v", st)
	}
	mustReserve(t, s, abScope, "a", 20_000, 20_000)
	if _, err := s.Settle(bg, abOrg, abScope, "a", 25_000, abTTL); err != nil { // an overshoot
		t.Fatal(err)
	}
	for _, micros := range []int64{0, 1} {
		st := mustReserve(t, s, abScope, fmt.Sprintf("b-%d", micros), micros, AgentBudgetNoLimit)
		if st.Status != AgentBudgetExhausted || st.SpentMicros != 25_000 {
			t.Fatalf("hold %d on an overspent scope: %+v", micros, st)
		}
		if st.RemainingMicros() != -5_000 {
			t.Fatalf("remaining %d, want -5000", st.RemainingMicros())
		}
	}
}

// SETTLE releases the hold and adds what the call cost, even when the hold is
// gone (a second call of one attempt, or a hold that expired): a run budget
// over-counts rather than misses spend. VOID only releases.
func TestAgentBudgetSettleAndVoid(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	mustReserve(t, s, abScope, "a", 10_000, 50_000)
	st, err := s.Settle(bg, abOrg, abScope, "a", 7_000, abTTL)
	if err != nil {
		t.Fatal(err)
	}
	if st.SpentMicros != 7_000 || st.HeldMicros != 0 || st.RemainingMicros() != 43_000 {
		t.Fatalf("after settle: %+v", st)
	}
	if st, err = s.Void(bg, abOrg, abScope, "a", abTTL); err != nil || st.SpentMicros != 7_000 || st.HeldMicros != 0 {
		t.Fatalf("void after settle: %+v, %v", st, err)
	}
	if st, err = s.Settle(bg, abOrg, abScope, "never-held", 2_000, abTTL); err != nil || st.SpentMicros != 9_000 {
		t.Fatalf("settle with no hold: %+v, %v", st, err)
	}
	mustReserve(t, s, abScope, "b", 10_000, AgentBudgetNoLimit)
	if st, err = s.Void(bg, abOrg, abScope, "b", abTTL); err != nil || st.SpentMicros != 9_000 || st.HeldMicros != 0 {
		t.Fatalf("void: %+v, %v", st, err)
	}
}

// RESERVE sweeps expired holds before it decides.
func TestAgentBudgetExpiredHoldsSwept(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	now := time.Now()
	s.now = func() time.Time { return now }
	if st, err := s.Reserve(bg, abOrg, abScope, "stale", 40_000, 50_000, time.Second, abTTL); err != nil || st.Status != AgentBudgetOK {
		t.Fatalf("reserve: %+v, %v", st, err)
	}
	if st := mustReserve(t, s, abScope, "blocked", 20_000, AgentBudgetNoLimit); st.Status != AgentBudgetExhausted {
		t.Fatalf("a hold over the room the stale hold leaves: %+v", st)
	}
	s.now = func() time.Time { return now.Add(2 * time.Second) }
	st := mustReserve(t, s, abScope, "fresh", 20_000, AgentBudgetNoLimit)
	if st.Status != AgentBudgetOK || st.HeldMicros != 20_000 {
		t.Fatalf("after the stale hold expired: %+v", st)
	}
}

// A stopped scope refuses every hold with the stop's reason.
func TestAgentBudgetStoppedScope(t *testing.T) {
	s, _, _ := agentBudgetTestStore(t)
	mustReserve(t, s, abScope, "before", 1_000, 50_000)
	if err := s.Stop(bg, abOrg, abScope, "tool_call_blocked:shell", abTTL); err != nil {
		t.Fatal(err)
	}
	st := mustReserve(t, s, abScope, "after", 1_000, 50_000)
	if st.Status != AgentBudgetStopped || !st.Stopped || st.StopReason != "tool_call_blocked:shell" || st.HeldMicros != 1_000 {
		t.Fatalf("a hold on a stopped scope: %+v", st)
	}
	peek, err := s.Peek(bg, abOrg, abScope)
	if err != nil || !peek.Stopped || peek.StopReason != "tool_call_blocked:shell" || peek.LimitMicros != 50_000 {
		t.Fatalf("peek: %+v, %v", peek, err)
	}
}

// The keys are abud:{<org>}:<scope>:limit|spent|held|holds|holdexp, each
// with the scope's sliding TTL.
func TestAgentBudgetKeysAndSlidingTTL(t *testing.T) {
	s, rdb, prefix := agentBudgetTestStore(t)
	base := prefix + "abud:{" + abOrg + "}:" + abScope + ":"
	if _, err := s.Reserve(bg, abOrg, abScope, "a", 10_000, 50_000, abHold, time.Minute); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Settle(bg, abOrg, abScope, "a", 3_000, time.Minute); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Reserve(bg, abOrg, abScope, "b", 10_000, AgentBudgetNoLimit, abHold, time.Hour); err != nil {
		t.Fatal(err)
	}
	for _, suffix := range []string{"limit", "spent", "held", "holds", "holdexp"} {
		ttl, err := rdb.TTL(bg, base+suffix).Result()
		if err != nil {
			t.Fatal(err)
		}
		if ttl < 59*time.Minute || ttl > time.Hour {
			t.Errorf("%s: TTL %v, want the scope TTL slid to 1h", suffix, ttl)
		}
	}
	if v, _ := rdb.Get(bg, base+"limit").Result(); v != "50000" {
		t.Errorf("limit = %q, want 50000", v)
	}
	if v, _ := rdb.Get(bg, base+"spent").Result(); v != "3000" {
		t.Errorf("spent = %q, want 3000", v)
	}
	if v, _ := rdb.HGet(bg, base+"holds", "b").Result(); v != "10000" {
		t.Errorf("holds[b] = %q, want 10000", v)
	}
}

// Without Redis every operation fails as unavailable; a malformed org or scope
// is refused before Redis is called.
func TestAgentBudgetUnavailableAndRefusedKeys(t *testing.T) {
	down := NewAgentBudgetStore(nil, "")
	if _, err := down.Reserve(bg, abOrg, abScope, "a", 1, AgentBudgetNoLimit, abHold, abTTL); !errors.Is(err, ErrAgentBudgetUnavailable) {
		t.Fatalf("reserve without Redis: %v", err)
	}
	if _, err := down.Peek(bg, abOrg, abScope); !errors.Is(err, ErrAgentBudgetUnavailable) {
		t.Fatalf("peek without Redis: %v", err)
	}
	s, _, _ := agentBudgetTestStore(t)
	for _, c := range []struct{ org, scope, id string }{
		{"", abScope, "a"}, {"o{x}", abScope, "a"}, {abOrg, "", "a"}, {abOrg, "s{1}", "a"}, {abOrg, "a b", "a"}, {abOrg, abScope, ""},
	} {
		if _, err := s.Reserve(bg, c.org, c.scope, c.id, 1, AgentBudgetNoLimit, abHold, abTTL); err == nil || errors.Is(err, ErrAgentBudgetUnavailable) {
			t.Errorf("org %q scope %q hold %q: err %v, want a refusal", c.org, c.scope, c.id, err)
		}
	}
}
