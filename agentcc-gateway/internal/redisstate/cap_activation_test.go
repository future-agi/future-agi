package redisstate

import (
	"encoding/json"
	"testing"
	"time"
)

// Decode the wire shape so this regression also runs against the pre-652 store.
func activatedBuckets(t *testing.T, level string, limit, amount int64) []CapBucket {
	t.Helper()
	raw, _ := json.Marshal([]map[string]any{{"level": level, "limit": limit, "amount": amount, "activated_at": "2026-10-27T00:00:00Z"}})
	var buckets []CapBucket
	if err := json.Unmarshal(raw, &buckets); err != nil {
		t.Fatal(err)
	}
	return buckets
}

func TestCapActivationStartsAtZero(t *testing.T) {
	s := capTestStore(t)
	buckets := activatedBuckets(t, "agent:a", 50000, 4000)
	result, err := s.Reserve(bg, "g8-org", "2026-10-27", "first", 1, buckets, time.Now().Add(time.Minute))
	if err != nil || result.Status != "ok" {
		t.Fatalf("first admission without history import: %+v %v", result, err)
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "first", []CapBucket{{Level: "agent:a", Amount: 4000}}); err != nil {
		t.Fatal(err)
	}
	// A cap edit changes the ceiling, never the activation or the count.
	buckets[0].Limit = 5000
	result, err = s.Reserve(bg, "g8-org", "2026-10-27", "edited", 2, buckets, time.Now().Add(time.Minute))
	if err != nil || result.Status != "exhausted" || result.Available != 1000 {
		t.Fatalf("cap edit reset spend: %+v %v", result, err)
	}
}

func TestCapRolloverPinsInflightCompletion(t *testing.T) {
	s := capTestStore(t)
	buckets := activatedBuckets(t, "key_id:k", 50000, 4000)
	for _, day := range []string{"2026-10-27", "2026-10-28"} {
		result, err := s.Reserve(bg, "g8-org", day, day, 1, buckets, time.Now().Add(time.Minute))
		if err != nil || result.Status != "ok" {
			t.Fatalf("%s: %+v %v", day, result, err)
		}
	}
	if err := s.Complete(bg, "g8-org", "2026-10-27", "2026-10-27", []CapBucket{{Level: "key_id:k", Amount: 3000}}); err != nil {
		t.Fatal(err)
	}
	old, _ := s.keys("g8-org", "2026-10-27")
	next, _ := s.keys("g8-org", "2026-10-28")
	if got := s.store.client.Redis().HGet(bg, old[0], "spent:key_id:k").Val(); got != "3000" {
		t.Fatalf("old day spend=%q", got)
	}
	if got := s.store.client.Redis().HGet(bg, next[0], "spent:key_id:k").Val(); got != "0" {
		t.Fatalf("new day spend=%q", got)
	}
}
