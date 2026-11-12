package redisstate

import (
	"fmt"
	"reflect"
	"testing"
	"time"
)

// A malformed value must be refused before policy writes, expiry sweeping, or
// any counter update. Testing both new admission and nonce replay prevents the
// idempotent fast path from blessing an invalid durable ledger.
func TestCapMalformedAdmissionStateIsNotRepaired(t *testing.T) {
	for _, target := range []struct {
		key   int
		field string
	}{
		{0, "spent:agent:a"}, {0, "held:agent:a"},
		{0, "uncertain:agent:a"}, {0, "overshot:agent:a"},
		{0, "limit:agent:a"}, {0, "version"},
		{4, "limit:agent:a"}, {4, "revision:agent:a"},
		{4, "revision"}, {4, "schema"}, {4, "policy:agent:a"},
	} {
		for _, value := range []string{"-4000", "0.5", "garbage", "", "1e3", "NaN", "inf", " 1", "+1", "9007199254740992"} {
			t.Run(fmt.Sprintf("key=%d/%s/%q", target.key, target.field, value), func(t *testing.T) {
				s := capTestStore(t)
				b := activatedBuckets(t, "agent:a", 5000, 4000)
				res, err := s.Reserve(bg, "g8-org", "2026-10-27", "first", 1, b, time.Now().Add(time.Minute))
				if err != nil || res.Status != "ok" {
					t.Fatalf("control=%+v %v", res, err)
				}
				keys, _ := s.keys("g8-org", "2026-10-27")
				rdb := s.store.client.Redis()
				if err := rdb.HSet(bg, keys[target.key], target.field, value).Err(); err != nil {
					t.Fatal(err)
				}
				beforeState := rdb.HGetAll(bg, keys[0]).Val()
				beforePolicy := rdb.HGetAll(bg, keys[4]).Val()
				for _, nonce := range []string{"next", "first"} {
					res, err = s.Reserve(bg, "g8-org", "2026-10-27", nonce, 1, b, time.Now().Add(time.Minute))
					if err != nil || res.Status != "unavailable" {
						t.Errorf("malformed state must explicitly refuse: result=%+v err=%v", res, err)
					}
					if !reflect.DeepEqual(beforeState, rdb.HGetAll(bg, keys[0]).Val()) || !reflect.DeepEqual(beforePolicy, rdb.HGetAll(bg, keys[4]).Val()) {
						t.Fatal("malformed state was partially mutated or repaired")
					}
				}
			})
		}
	}
}

func TestCapCorruptExpiryBucketCannotPartiallyFence(t *testing.T) {
	s := capTestStore(t)
	b := activatedBuckets(t, "agent:a", 5000, 4000)
	res, err := s.Reserve(bg, "g8-org", "2026-10-27", "first", 1, b, time.Now().Add(time.Minute))
	if err != nil || res.Status != "ok" {
		t.Fatalf("control=%+v %v", res, err)
	}
	keys, _ := s.keys("g8-org", "2026-10-27")
	rdb := s.store.client.Redis()
	if err := rdb.HSet(bg, keys[0], "uncertain:agent:a", "-1").Err(); err != nil {
		t.Fatal(err)
	}
	if err := rdb.Do(bg, "ZADD", keys[2], 0, "first").Err(); err != nil {
		t.Fatal(err)
	}
	// This request does not name the corrupt bucket. The expiry sweep does.
	res, err = s.Reserve(bg, "g8-org", "2026-10-27", "next", 1, activatedBuckets(t, "key_id:k", 5000, 4000), time.Now().Add(time.Minute))
	if err != nil || res.Status != "unavailable" {
		t.Fatalf("corrupt expiry bucket admitted: %+v %v", res, err)
	}
	if rdb.HLen(bg, keys[5]).Val() != 0 || rdb.ZCard(bg, keys[2]).Val() != 1 || rdb.HGet(bg, keys[0], "uncertain:agent:a").Val() != "-1" {
		t.Fatal("expiry sweep repaired or partially fenced invalid state")
	}
}
