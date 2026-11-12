package redisstate

// Permanent round-four verifier regressions and their extensions. Synthetic
// Redis only. Redis keeps a script's earlier writes when a later command
// fails, so every refusal below must leave the ledger byte-identical.

import (
	"fmt"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

const round4Day = "2026-10-27"

// capKeySnapshot renders a key's type and full contents independently of
// encoding. DUMP cannot compare daily keys: the policy registry's manifest
// field embeds the whole state key, so that hash exceeds the listpack limit,
// becomes a hashtable, and its DUMP order moves with incremental rehashing on
// plain reads although no field changed.
func capKeySnapshot(t *testing.T, rdb redis.UniversalClient, key string) string {
	t.Helper()
	kind, err := rdb.Type(bg, key).Result()
	if err != nil {
		t.Fatal(err)
	}
	var body string
	switch kind {
	case "none":
	case "string":
		var value string
		value, err = rdb.Get(bg, key).Result()
		body = fmt.Sprintf("%q", value)
	case "hash":
		var fields map[string]string
		fields, err = rdb.HGetAll(bg, key).Result()
		body = fmt.Sprintf("%q", fields) // fmt sorts map keys
	case "zset":
		var members []redis.Z
		members, err = rdb.ZRangeWithScores(bg, key, 0, -1).Result()
		for _, m := range members {
			body += fmt.Sprintf("%q=%v;", fmt.Sprint(m.Member), m.Score)
		}
	case "set":
		var members []string
		members, err = rdb.SMembers(bg, key).Result()
		sort.Strings(members)
		body = fmt.Sprintf("%q", members)
	default:
		t.Fatalf("unexpected Redis type %s for %s", kind, key)
	}
	if err != nil {
		t.Fatal(err)
	}
	return kind + ":" + body
}

func dailySnapshot(t *testing.T, s *CapHoldStore) []string {
	t.Helper()
	keys, err := s.keys("g8-org", round4Day)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, key := range keys {
		out = append(out, capKeySnapshot(t, s.store.client.Redis(), key))
	}
	return out
}

func round4DailyReservation(t *testing.T, s *CapHoldStore) []string {
	t.Helper()
	result, err := s.Reserve(bg, "g8-org", round4Day, "first", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
	if err != nil || result.Status != "ok" {
		t.Fatalf("control=%+v %v", result, err)
	}
	keys, _ := s.keys("g8-org", round4Day)
	return keys
}

// A completion refused for a noncanonical spend counter must not release the
// reservation first: Fence must still find it.
func TestR4DailyFailedCompleteRetainsFenceableReservation(t *testing.T) {
	for _, raw := range []string{"4000.0", "4e3", " 4000", "4000.5", "-0", "00"} {
		t.Run(fmt.Sprintf("spent=%q", raw), func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := rdb.HSet(bg, keys[0], "spent:agent:a", raw).Err(); err != nil {
				t.Fatal(err)
			}
			before := dailySnapshot(t, s)
			completeErr := s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}})
			after := dailySnapshot(t, s)
			fenceErr := s.Fence(bg, "g8-org", round4Day, "first")
			t.Logf("complete=%v mutated=%v fence=%v spent=%q held=%q holds=%d terminal=%d uncertain=%v", completeErr, !reflect.DeepEqual(before, after), fenceErr, rdb.HGet(bg, keys[0], "spent:agent:a").Val(), rdb.HGet(bg, keys[0], "held:agent:a").Val(), rdb.HLen(bg, keys[1]).Val(), rdb.HLen(bg, keys[3]).Val(), rdb.HGetAll(bg, keys[5]).Val())
			if completeErr == nil {
				t.Error("noncanonical daily counter accepted during completion")
			}
			if !reflect.DeepEqual(before, after) {
				t.Error("refused Complete partially mutated accounting and lost the reservation")
			}
			if fenceErr != nil {
				t.Error("refused Complete cannot be fenced")
			}
		})
	}
}

// After a refused completion is fenced and the counter is repaired, the same
// nonce's authoritative Complete releases the hold and lifts the fence once.
func TestCapRefusedCompleteSettlesAfterRepair(t *testing.T) {
	for _, raw := range []string{"4000.0", "-0", "9007199254740992"} {
		t.Run(fmt.Sprintf("spent=%q", raw), func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := rdb.HSet(bg, keys[0], "spent:agent:a", raw).Err(); err != nil {
				t.Fatal(err)
			}
			actual := []CapBucket{{Level: "agent:a", Amount: 1000}}
			if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err == nil {
				t.Fatal("malformed spend accepted")
			}
			if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
				t.Fatalf("refused completion could not be fenced: %v", err)
			}
			if res, err := s.Reserve(bg, "g8-org", round4Day, "blocked", 1, activatedBuckets(t, "agent:a", 50000, 1), time.Now().Add(time.Minute)); err != nil || res.Status == "ok" {
				t.Fatalf("fenced or malformed ledger admitted work: %+v %v", res, err)
			}
			if err := rdb.HSet(bg, keys[0], "spent:agent:a", "0").Err(); err != nil {
				t.Fatal(err)
			}
			if res, err := s.Reserve(bg, "g8-org", round4Day, "blocked", 1, activatedBuckets(t, "agent:a", 50000, 1), time.Now().Add(time.Minute)); err != nil || res.Status != "unavailable" {
				t.Fatalf("counter-only repair reopened fenced work: %+v %v", res, err)
			}
			if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err != nil {
				t.Fatalf("authoritative completion after repair refused: %v", err)
			}
			state := rdb.HGetAll(bg, keys[0]).Val()
			if state["spent:agent:a"] != "1000" || state["held:agent:a"] != "0" || state["uncertain:agent:a"] != "0" || rdb.HLen(bg, keys[1]).Val() != 0 || rdb.ZCard(bg, keys[2]).Val() != 0 || rdb.HLen(bg, keys[3]).Val() != 1 || rdb.HLen(bg, keys[5]).Val() != 0 {
				t.Fatalf("late settlement did not resolve exactly once: state=%v holds=%d expiry=%d terminal=%d fences=%d", state, rdb.HLen(bg, keys[1]).Val(), rdb.ZCard(bg, keys[2]).Val(), rdb.HLen(bg, keys[3]).Val(), rdb.HLen(bg, keys[5]).Val())
			}
			if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err != nil {
				t.Fatalf("terminal replay refused: %v", err)
			}
			if res, err := s.Reserve(bg, "g8-org", round4Day, "after", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
				t.Fatalf("resolved ledger refused valid work: %+v %v", res, err)
			}
		})
	}
}

// Every field release and settlement read or write is preflighted, including
// the fenced uncertainty counter, the resulting spend and the stored records.
func TestCapDailyCompletionPreflight(t *testing.T) {
	type scenario struct {
		name  string
		setup func(rdb redis.UniversalClient, keys []string) error
		fence bool
	}
	hset := func(i int, field, value string) func(redis.UniversalClient, []string) error {
		return func(rdb redis.UniversalClient, keys []string) error { return rdb.HSet(bg, keys[i], field, value).Err() }
	}
	for _, sc := range []scenario{
		{name: "spent-overflow", setup: hset(0, "spent:agent:a", "9007199254740991")},
		{name: "spent-missing", setup: func(rdb redis.UniversalClient, keys []string) error {
			return rdb.HDel(bg, keys[0], "spent:agent:a").Err()
		}},
		{name: "held-below-reservation", setup: hset(0, "held:agent:a", "3999")},
		{name: "held-noncanonical", setup: hset(0, "held:agent:a", "4000.0")},
		{name: "overshot-noncanonical", setup: hset(0, "overshot:agent:a", "1.0")},
		{name: "limit-noncanonical", setup: hset(0, "limit:agent:a", "5e4")},
		{name: "fenced-uncertainty-zero", fence: true, setup: hset(0, "uncertain:agent:a", "0")},
		{name: "fenced-uncertainty-noncanonical", fence: true, setup: hset(0, "uncertain:agent:a", "1.0")},
		{name: "fence-record-noncanonical", fence: true, setup: hset(5, "first", "x")},
		{name: "reservation-fraction", setup: hset(1, "first", `[{"level":"agent:a","amount":4000.5}]`)},
		{name: "reservation-negative", setup: hset(1, "first", `[{"level":"agent:a","amount":-4000}]`)},
		{name: "reservation-not-json", setup: hset(1, "first", `{`)},
		{name: "reservation-object", setup: hset(1, "first", `{"level":"agent:a","amount":4000}`)},
		{name: "reservation-duplicate-level", setup: hset(1, "first", `[{"level":"agent:a","amount":2000},{"level":"agent:a","amount":2000}]`)},
	} {
		t.Run(sc.name, func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if sc.fence {
				if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
					t.Fatal(err)
				}
			}
			if err := sc.setup(rdb, keys); err != nil {
				t.Fatal(err)
			}
			before := dailySnapshot(t, s)
			err := s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}})
			if err == nil {
				t.Error("unsafe completion accepted")
			}
			if !reflect.DeepEqual(before, dailySnapshot(t, s)) {
				t.Fatalf("refused completion partially mutated the ledger: err=%v state=%v holds=%d fences=%v", err, rdb.HGetAll(bg, keys[0]).Val(), rdb.HLen(bg, keys[1]).Val(), rdb.HGetAll(bg, keys[5]).Val())
			}
		})
	}
}

// A key of the wrong Redis type is refused by admission, completion and fence
// before any write, including an admission whose expiry sweep would fence.
func TestCapDailyWrongTypeKeysNeverMutate(t *testing.T) {
	for key := 0; key < 6; key++ {
		for _, op := range []string{"reserve", "reserve-expired", "complete", "fence"} {
			t.Run(fmt.Sprintf("key=%d/%s", key, op), func(t *testing.T) {
				s := capTestStore(t)
				keys := round4DailyReservation(t, s)
				rdb := s.store.client.Redis()
				if op == "reserve-expired" {
					if err := rdb.ZAdd(bg, keys[2], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
						t.Fatal(err)
					}
				}
				if err := rdb.Del(bg, keys[key]).Err(); err != nil {
					t.Fatal(err)
				}
				if err := rdb.Set(bg, keys[key], "4000.0", 0).Err(); err != nil {
					t.Fatal(err)
				}
				before := dailySnapshot(t, s)
				var err error
				switch op {
				case "reserve", "reserve-expired":
					var res CapHoldResult
					res, err = s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
					if err == nil && res.Status == "ok" {
						t.Errorf("wrong-type state admitted work: %+v", res)
					}
				case "complete":
					err = s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}})
					if err == nil {
						t.Error("wrong-type state accepted a completion")
					}
				case "fence":
					err = s.Fence(bg, "g8-org", round4Day, "first")
					if key == 4 {
						// Fence reads no policy, so a malformed registry must not stop
						// it from blocking admission for this reservation.
						if err != nil || !rdb.HExists(bg, keys[5], "first").Val() {
							t.Fatalf("fence refused although it never reads the policy registry: %v", err)
						}
						return
					}
					if err == nil {
						t.Error("wrong-type state accepted a fence")
					}
				}
				if !reflect.DeepEqual(before, dailySnapshot(t, s)) {
					t.Fatalf("wrong-type refusal partially mutated the ledger: err=%v", err)
				}
			})
		}
	}
}

// Auxiliary strict-run keys of the wrong type: the uncertainty set during
// reservation and completion, and the expiry index during legacy adoption.
func TestR4MalformedRunAuxiliaryStateNeverMutates(t *testing.T) {
	for _, scenario := range []string{"uncertainty-reserve", "uncertainty-complete", "expiry-legacy-adoption"} {
		t.Run(scenario, func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "sol-auxiliary"
			var err error
			if scenario == "expiry-legacy-adoption" {
				_, err = s.Reserve(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
			} else {
				_, err = s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
			}
			if err != nil {
				t.Fatal(err)
			}
			keys := s.strictKeys("g8-org", scope)
			target := keys[8]
			if scenario == "expiry-legacy-adoption" {
				target = keys[4]
			}
			if err := s.client.Redis().Set(bg, target, "4000.0", 0).Err(); err != nil {
				t.Fatal(err)
			}
			before := strictLedgerSnapshot(t, s, scope)
			if scenario == "uncertainty-complete" {
				_, err = s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour)
			} else {
				_, err = s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 40000, time.Minute, time.Hour)
			}
			changed := !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope))
			t.Logf("err=%v mutated=%v limit=%q spent=%q held=%q holds=%d terminal=%d strict=%q", err, changed, s.client.Redis().Get(bg, keys[0]).Val(), s.client.Redis().Get(bg, keys[1]).Val(), s.client.Redis().Get(bg, keys[2]).Val(), s.client.Redis().HLen(bg, keys[3]).Val(), s.client.Redis().HLen(bg, keys[6]).Val(), s.client.Redis().Get(bg, keys[7]).Val())
			if err == nil {
				t.Error("malformed auxiliary state accepted")
			}
			if changed {
				t.Error("refusal partially mutated strict run ledger")
			}
		})
	}
}

// Every strict key, in turn, holds another Redis type (and the adoption
// marker a value this code never writes). Peek, reservation, nonce replay,
// legacy adoption and completion all refuse without any write; fence refuses
// for the keys it reads or writes and fences despite the others.
func TestStrictRunWrongTypeKeysNeverMutate(t *testing.T) {
	type corruption struct {
		name  string
		key   int
		apply func(rdb redis.UniversalClient, key string) error
	}
	replace := func(write func(rdb redis.UniversalClient, key string) error) func(redis.UniversalClient, string) error {
		return func(rdb redis.UniversalClient, key string) error {
			if err := rdb.Del(bg, key).Err(); err != nil {
				return err
			}
			return write(rdb, key)
		}
	}
	asHash := replace(func(rdb redis.UniversalClient, key string) error { return rdb.HSet(bg, key, "f", "1").Err() })
	asString := replace(func(rdb redis.UniversalClient, key string) error { return rdb.Set(bg, key, "4000.0", 0).Err() })
	var corruptions []corruption
	for _, key := range []int{0, 1, 2, 5, 7} {
		corruptions = append(corruptions, corruption{fmt.Sprintf("key=%d/hash", key), key, asHash})
	}
	for _, key := range []int{3, 4, 6, 8} {
		corruptions = append(corruptions, corruption{fmt.Sprintf("key=%d/string", key), key, asString})
	}
	for _, marker := range []string{"2", "", "1.0"} {
		value := marker
		corruptions = append(corruptions, corruption{fmt.Sprintf("marker=%q", marker), 7, func(rdb redis.UniversalClient, key string) error { return rdb.Set(bg, key, value, 0).Err() }})
	}
	for _, legacy := range []bool{false, true} {
		for _, c := range corruptions {
			for _, op := range []string{"peek", "reserve", "replay", "complete", "fence"} {
				t.Run(fmt.Sprintf("legacy=%v/%s/%s", legacy, c.name, op), func(t *testing.T) {
					s := capTestStore(t).store
					const scope = "wrong-type"
					var err error
					if legacy {
						_, err = s.Reserve(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
					} else {
						_, err = s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
					}
					if err != nil {
						t.Fatal(err)
					}
					keys := s.strictKeys("g8-org", scope)
					rdb := s.client.Redis()
					if err := c.apply(rdb, keys[c.key]); err != nil {
						t.Fatal(err)
					}
					before := strictLedgerSnapshot(t, s, scope)
					var res AgentBudgetState
					switch op {
					case "peek":
						res, err = s.PeekStrict(bg, "g8-org", scope)
					case "reserve":
						res, err = s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 40000, time.Minute, time.Hour)
					case "replay":
						res, err = s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 40000, time.Minute, time.Hour)
					case "complete":
						res, err = s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour)
					case "fence":
						err = s.FenceStrict(bg, "g8-org", scope, "first")
						if c.key != 3 && c.key != 4 && c.key != 6 && c.key != 8 {
							// Fence never reads limit, spent, held, the stop reason
							// or the marker, so their corruption must not stop it
							// from marking the retained hold uncertain.
							if err != nil || !rdb.SIsMember(bg, keys[8], "first").Val() {
								t.Fatalf("fence refused although it never reads that key: %v", err)
							}
							return
						}
					}
					if err == nil {
						t.Errorf("wrong-type strict state accepted: %+v", res)
					}
					if !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
						t.Fatalf("wrong-type refusal partially mutated strict ledger: err=%v limit=%q spent=%q held=%q holds=%d", err, rdb.Get(bg, keys[0]).Val(), rdb.Get(bg, keys[1]).Val(), rdb.Get(bg, keys[2]).Val(), rdb.HLen(bg, keys[3]).Val())
					}
				})
			}
		}
	}
}

// A conflicting nonce replay is refused before adoption, expiry fencing or a
// policy (limit) update, not after them.
func TestStrictRunConflictingReplayNeverMutates(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		t.Run(fmt.Sprintf("legacy=%v", legacy), func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "conflict"
			var err error
			if legacy {
				_, err = s.Reserve(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
			} else {
				_, err = s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
			}
			if err != nil {
				t.Fatal(err)
			}
			keys := s.strictKeys("g8-org", scope)
			if err := s.client.Redis().ZAdd(bg, keys[4], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
				t.Fatal(err)
			}
			before := strictLedgerSnapshot(t, s, scope)
			if _, err := s.ReserveStrict(bg, "g8-org", scope, "first", 5000, 40000, time.Minute, time.Hour); err == nil {
				t.Error("conflicting replay accepted")
			}
			if !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
				t.Fatal("conflicting replay adopted, fenced or lowered the limit before refusing")
			}
		})
	}
}
