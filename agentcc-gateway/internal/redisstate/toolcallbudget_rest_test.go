package redisstate

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// toolCallTestBudget is a ToolCallBudget on the test Redis (TEST_REDIS_ADDR,
// TEST_REDIS_DB) under the prefix test:V6:<test>:<nonce>:, whose keys are
// deleted when the test ends (SCAN and DEL, never FLUSHDB). It skips when
// TEST_REDIS_ADDR is unset, so it never falls back to another Redis.
func toolCallTestBudget(t *testing.T) (*ToolCallBudget, redis.UniversalClient, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := NewClient(Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	prefix := fmt.Sprintf("test:V6:%s:%d:", t.Name(), time.Now().UnixNano())
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
	return NewToolCallBudget(client, prefix), rdb, prefix
}

const (
	tcbOrg   = "org-v6"
	tcbScope = "run-R1"
	tcbTTL   = time.Hour
)

// The run's tool-call counter is MC.6's mtb:{<org>}:<scope>:calls, exactly,
// under the store's prefix (none in production), so M19 (MA5) reads and
// writes the same key; the search counter is its sibling in the same family.
func TestToolCallBudgetKeyMatchesMC6(t *testing.T) {
	if got := NewToolCallBudget(nil, "").Key(tcbOrg, tcbScope, ToolCounterCalls); got != "mtb:{org-v6}:run-R1:calls" {
		t.Fatalf("production calls key = %q, want mtb:{org-v6}:run-R1:calls", got)
	}
	b, rdb, prefix := toolCallTestBudget(t)
	st, err := b.Take(bg, tcbOrg, tcbScope, 1, ToolCounterSearches, 2, ToolCallNoLimit, tcbTTL)
	if err != nil || !st.Taken || st.Calls != 1 || st.Count != 2 {
		t.Fatalf("take = %+v, %v; want taken, calls 1, searches 2", st, err)
	}
	for key, want := range map[string]string{
		prefix + "mtb:{org-v6}:run-R1:calls":    "1",
		prefix + "mtb:{org-v6}:run-R1:searches": "2",
	} {
		got, err := rdb.Get(bg, key).Result()
		if err != nil || got != want {
			t.Errorf("GET %s = %q, %v; want %q", key, got, err, want)
		}
		ttl, err := rdb.TTL(bg, key).Result()
		if err != nil || ttl <= 0 || ttl > tcbTTL {
			t.Errorf("TTL %s = %v, %v; want (0, %v]", key, ttl, err, tcbTTL)
		}
	}
}

// A take that would pass the limit changes nothing and reports the count it
// would have passed; within the limit it counts.
func TestToolCallBudgetRefusesPastLimit(t *testing.T) {
	b, _, _ := toolCallTestBudget(t)
	for i, want := range []bool{true, true, false} {
		st, err := b.Take(bg, tcbOrg, tcbScope, 1, ToolCounterSearches, 1, 2, tcbTTL)
		if err != nil || st.Taken != want {
			t.Fatalf("take %d = %+v, %v; want taken=%v", i+1, st, err, want)
		}
	}
	calls, searches, err := b.Counts(bg, tcbOrg, tcbScope, ToolCounterSearches)
	if err != nil || calls != 2 || searches != 2 {
		t.Fatalf("counts = %d calls, %d searches, %v; want 2, 2 (the refused take counts nothing)", calls, searches, err)
	}
	// A take of several at once must fit whole.
	if st, err := b.Take(bg, tcbOrg, "run-R2", 1, ToolCounterSearches, 3, 2, tcbTTL); err != nil || st.Taken || st.Count != 0 {
		t.Fatalf("3 searches under a limit of 2 = %+v, %v; want refused with nothing counted", st, err)
	}
}

// Recorded counts (native searches) are added whatever the limit, and a
// release gives back a take that never reached a vendor, never below zero.
func TestToolCallBudgetAddAndRelease(t *testing.T) {
	b, _, _ := toolCallTestBudget(t)
	if st, err := b.Take(bg, tcbOrg, tcbScope, 0, ToolCounterSearches, 5, ToolCallNoLimit, tcbTTL); err != nil || st.Count != 5 || st.Calls != 0 {
		t.Fatalf("add 5 = %+v, %v; want searches 5, calls untouched", st, err)
	}
	if st, err := b.Take(bg, tcbOrg, tcbScope, 1, ToolCounterSearches, 1, 3, tcbTTL); err != nil || st.Taken {
		t.Fatalf("take at 5 of 3 = %+v, %v; want refused", st, err)
	}
	if err := b.Release(bg, tcbOrg, tcbScope, 2, ToolCounterSearches, 9, tcbTTL); err != nil {
		t.Fatal(err)
	}
	calls, searches, err := b.Counts(bg, tcbOrg, tcbScope, ToolCounterSearches)
	if err != nil || calls != 0 || searches != 0 {
		t.Fatalf("after an over-release: %d calls, %d searches, %v; want 0, 0", calls, searches, err)
	}
}

// Concurrent takes never count past the limit.
func TestToolCallBudgetConcurrentTakesHoldTheLimit(t *testing.T) {
	b, _, _ := toolCallTestBudget(t)
	var wg sync.WaitGroup
	var mu sync.Mutex
	taken := 0
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			st, err := b.Take(bg, tcbOrg, tcbScope, 1, ToolCounterSearches, 1, 5, tcbTTL)
			if err == nil && st.Taken {
				mu.Lock()
				taken++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if _, searches, err := b.Counts(bg, tcbOrg, tcbScope, ToolCounterSearches); err != nil || taken != 5 || searches != 5 {
		t.Fatalf("20 concurrent takes under a limit of 5: %d taken, %d counted, %v; want 5 and 5", taken, searches, err)
	}
}

// A store without Redis, or an org or scope that cannot be keyed, fails
// with the store's error and counts nothing.
func TestToolCallBudgetUnavailableAndBadKeys(t *testing.T) {
	if _, err := NewToolCallBudget(nil, "").Take(bg, tcbOrg, tcbScope, 1, ToolCounterSearches, 1, 1, tcbTTL); !errors.Is(err, ErrToolCallBudgetUnavailable) {
		t.Fatalf("no client: err = %v, want ErrToolCallBudgetUnavailable", err)
	}
	b, _, _ := toolCallTestBudget(t)
	for _, k := range [][3]string{{"", tcbScope, ToolCounterSearches}, {"o{x}", tcbScope, ToolCounterSearches},
		{tcbOrg, "", ToolCounterSearches}, {tcbOrg, "a b", ToolCounterSearches}, {tcbOrg, tcbScope, "calls"}, {tcbOrg, tcbScope, "x:y"}} {
		if _, err := b.Take(bg, k[0], k[1], 1, k[2], 1, 1, tcbTTL); err == nil {
			t.Errorf("take on org %q scope %q counter %q succeeded; want refused", k[0], k[1], k[2])
		}
	}
}
