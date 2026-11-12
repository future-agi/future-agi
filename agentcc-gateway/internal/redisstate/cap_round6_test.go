package redisstate

import (
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Permanent round-six verifier probes (synthetic Redis only).
// Losing one auxiliary key must not reopen work whose outcome is unknown.
func TestR6StrictLostUncertaintyNeverReopens(t *testing.T) {
	for _, refence := range []bool{false, true} {
		t.Run(fmt.Sprintf("refence=%v", refence), func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "sol-r6-lost-strict-uncertainty"
			res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour)
			if err != nil || res.Status != AgentBudgetOK {
				t.Fatalf("control: %+v %v", res, err)
			}
			if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
				t.Fatal(err)
			}
			keys := s.strictKeys("g8-org", scope)
			rdb := s.client.Redis()
			if err := rdb.Del(bg, keys[8]).Err(); err != nil {
				t.Fatal(err)
			}
			if refence {
				if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
					t.Fatal(err)
				}
			}
			before := strictLedgerSnapshot(t, s, scope)
			res, err = s.ReserveStrict(bg, "g8-org", scope, "next", 40000, 50000, time.Hour, time.Hour)
			t.Logf("result=%+v err=%v uncertain=%d expiry=%d firstExists=%v nextExists=%v", res, err, rdb.SCard(bg, keys[8]).Val(), rdb.ZCard(bg, keys[4]).Val(), rdb.HExists(bg, keys[3], "first").Val(), rdb.HExists(bg, keys[3], "next").Val())
			if err == nil && res.Status == AgentBudgetOK {
				t.Error("deleting only uncertainty set reopened a fenced strict scope")
			}
			if !refence && !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
				t.Error("new admission mutated lost-uncertainty ledger")
			}
		})
	}
}

// Missing/zero counter after two fences must stay closed throughout successive
// completions, even if nobody repeats Fence before the first completion.
func TestR6DailyUnderCountNeverReopens(t *testing.T) {
	for _, counter := range []string{"missing", "0", "1"} {
		t.Run(counter, func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			b := activatedBuckets(t, "agent:a", 50000, 4000)
			res, err := s.Reserve(bg, "g8-org", round4Day, "second", 1, b, time.Now().Add(time.Hour))
			if err != nil || res.Status != "ok" {
				t.Fatalf("control %+v %v", res, err)
			}
			for _, id := range []string{"first", "second"} {
				if err := s.Fence(bg, "g8-org", round4Day, id); err != nil {
					t.Fatal(err)
				}
			}
			if counter == "missing" {
				err = rdb.HDel(bg, keys[0], "uncertain:agent:a").Err()
			} else {
				err = rdb.HSet(bg, keys[0], "uncertain:agent:a", counter).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			actual := []CapBucket{{Level: "agent:a", Amount: 1000}}
			err = s.Complete(bg, "g8-org", round4Day, "first", actual)
			if counter == "1" && err != nil {
				t.Fatal(err)
			}
			before := dailySnapshot(t, s)
			res, err = s.Reserve(bg, "g8-org", round4Day, "next", 1, b, time.Now().Add(time.Hour))
			if err == nil && res.Status == "ok" {
				t.Fatal("other unresolved fence reopened after undercounted completion")
			}
			if !reflect.DeepEqual(before, dailySnapshot(t, s)) {
				t.Error("refusal mutated lost counter state")
			}
			if err := s.Fence(bg, "g8-org", round4Day, "second"); err != nil {
				t.Fatal(err)
			}
			if counter != "1" {
				if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
					t.Fatal(err)
				}
				if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.Complete(bg, "g8-org", round4Day, "second", actual); err != nil {
				t.Fatal(err)
			}
			if got := rdb.HGet(bg, keys[0], "uncertain:agent:a").Val(); got != "0" {
				t.Fatalf("unresolved uncertainty %q", got)
			}
		})
	}
}

// The new earlier activation check must cover the second requested bucket,
// with an expired hold and both a new nonce and the original replay nonce.
func TestR6ActivationSecondBucketNeverMutates(t *testing.T) {
	for _, nonce := range []string{"first", "next"} {
		for _, activation := range []string{"", "bad-date", "2026-10-27T00:00:00.5Z"} {
			t.Run(fmt.Sprintf("%s/%q", nonce, activation), func(t *testing.T) {
				s := capTestStore(t)
				b := activatedBuckets(t, "agent:a", 50000, 4000)
				b = append(b, activatedBuckets(t, "key:b", 50000, 4000)...)
				res, err := s.Reserve(bg, "g8-org", round4Day, "first", 1, b, time.Now().Add(time.Hour))
				if err != nil || res.Status != "ok" {
					t.Fatalf("control %+v %v", res, err)
				}
				keys, _ := s.keys("g8-org", round4Day)
				rdb := s.store.client.Redis()
				if err := rdb.HSet(bg, keys[4], "activation:key:b", activation).Err(); err != nil {
					t.Fatal(err)
				}
				if err := rdb.ZAdd(bg, keys[2], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
					t.Fatal(err)
				}
				before := dailySnapshot(t, s)
				res, err = s.Reserve(bg, "g8-org", round4Day, nonce, 1, b, time.Now().Add(time.Hour))
				if err == nil && res.Status != "unavailable" {
					t.Fatalf("activation admitted: %+v", res)
				}
				if !reflect.DeepEqual(before, dailySnapshot(t, s)) {
					t.Error("second-bucket activation validation followed mutation")
				}
			})
		}
	}
}

// Explicitly exercise the ordering the writer left untested. Invalid fence
// evidence may conservatively outrank stale, but neither path may mutate.
func TestR6StaleMissingUncertaintyNeverMutates(t *testing.T) {
	s := capTestStore(t)
	keys := round4DailyReservation(t, s)
	rdb := s.store.client.Redis()
	if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
		t.Fatal(err)
	}
	if err := rdb.HDel(bg, keys[0], "uncertain:agent:a").Err(); err != nil {
		t.Fatal(err)
	}
	if err := rdb.HSet(bg, keys[4], "revision", 2).Err(); err != nil {
		t.Fatal(err)
	}
	before := dailySnapshot(t, s)
	res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Hour))
	if err != nil || res.Status != "unavailable" {
		t.Fatalf("want conservative unavailable: %+v %v", res, err)
	}
	if !reflect.DeepEqual(before, dailySnapshot(t, s)) {
		t.Fatal("stale missing-uncertainty refusal mutated ledger")
	}
}
