package redisstate

import (
	"context"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func capTestStore(t *testing.T) *CapHoldStore {
	t.Helper()
	if os.Getenv("TEST_REDIS_ADDR") == "" {
		t.Fatal("strict cap tests require synthetic Redis")
	}
	s, _, _ := agentBudgetTestStore(t)
	return &CapHoldStore{store: s}
}
func seedCap(t *testing.T, s *CapHoldStore, level string, n int64) {
	t.Helper()
	buckets := activatedBuckets(t, level, 1000000, n)
	result, err := s.Reserve(context.Background(), "g8-org", "2026-10-27", "fixture-"+level, 1, buckets, time.Now().Add(time.Minute))
	if err != nil || result.Status != "ok" {
		t.Fatalf("fixture admission: %+v %v", result, err)
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "fixture-"+level, []CapBucket{{Level: level, Amount: n}}); err != nil {
		t.Fatal(err)
	}
}
func TestCapHoldAllLevelsOrNone(t *testing.T) {
	s := capTestStore(t)
	seedCap(t, s, "agent:a", 0)
	seedCap(t, s, "key_id:k", 49000)
	res, err := s.Reserve(bg, "g8-org", "2026-10-27", "n", 1, append(activatedBuckets(t, "agent:a", 50000, 4000), activatedBuckets(t, "key_id:k", 50000, 4000)...), time.Now().Add(time.Minute))
	if err != nil || res.Status != "exhausted" || res.Level != "key_id:k" || res.Available != 1000 {
		t.Fatalf("result=%+v err=%v", res, err)
	}
	keys, _ := s.keys("g8-org", "2026-10-27")
	if s.store.client.Redis().HGet(bg, keys[0], "held:agent:a").Val() != "0" {
		t.Fatal("partial agent reservation")
	}
}
func TestCapBurstAcrossReplicas(t *testing.T) {
	s := capTestStore(t)
	other := NewCapHoldStore(s.store.client, s.store.prefix)
	seedCap(t, s, "agent:a", 0)
	seedCap(t, s, "key_id:k", 0)
	var accepted atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			replica := s
			if i%2 == 1 {
				replica = other
			}
			nonce := fmt.Sprintf("nonce-%d", i)
			res, err := replica.Reserve(bg, "g8-org", "2026-10-27", nonce, 1, append(activatedBuckets(t, "agent:a", 50000, 4000), activatedBuckets(t, "key_id:k", 50000, 4000)...), time.Now().Add(time.Minute))
			if err != nil {
				t.Error(err)
				return
			}
			if res.Status == "exhausted" {
				if res.Available >= res.Required {
					t.Error("invalid refusal evidence")
				}
				return
			}
			if res.Status != "ok" {
				t.Errorf("unexpected %+v", res)
				return
			}
			accepted.Add(1)
			terminal := []CapBucket{{Level: "agent:a", Amount: 4000}, {Level: "key_id:k", Amount: 4000}}
			if err := replica.Complete(bg, "g8-org", "2026-10-27", nonce, terminal); err != nil {
				t.Error(err)
			}
			if err := replica.Complete(bg, "g8-org", "2026-10-27", nonce, terminal); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	if accepted.Load() != 12 {
		t.Fatalf("completed=%d want 12", accepted.Load())
	}
	keys, _ := s.keys("g8-org", "2026-10-27")
	for _, level := range []string{"agent:a", "key_id:k"} {
		if v := s.store.client.Redis().HGet(bg, keys[0], "spent:"+level).Val(); v != "48000" {
			t.Errorf("%s spent=%s", level, v)
		}
		if v := s.store.client.Redis().HGet(bg, keys[0], "held:"+level).Val(); v != "0" {
			t.Errorf("%s held=%s", level, v)
		}
	}
}
func TestCapUnavailableFailsClosed(t *testing.T) {
	s := capTestStore(t)
	res, err := s.Reserve(bg, "g8-org", "2026-10-27", "n", 1, []CapBucket{{Level: "key_id:k", Limit: 50000, Amount: 4000}}, time.Now().Add(time.Minute))
	if err == nil || res.Status == "ok" {
		t.Fatalf("missing activation admission=%+v err=%v", res, err)
	}
}
func TestCapRunTerminalReplaySafe(t *testing.T) {
	s := capTestStore(t).store
	_, err := s.ReserveStrict(bg, "g8-org", "run", "nonce", 4000, 50000, time.Minute, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		res, err := s.Complete(bg, "g8-org", "run", "nonce", 3000, 24*time.Hour)
		if err != nil || res.SpentMicros != 3000 || res.HeldMicros != 0 {
			t.Fatalf("replay %d: %+v %v", i, res, err)
		}
	}
	if _, err := s.Complete(bg, "g8-org", "run", "nonce", 0, 24*time.Hour); err == nil {
		t.Fatal("conflicting terminal accepted")
	}
}
