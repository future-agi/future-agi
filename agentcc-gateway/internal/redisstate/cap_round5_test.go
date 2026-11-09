package redisstate

// Permanent round-five verifier regressions and their extensions. Synthetic
// Redis only. The TestSolR5 probes keep their verifier names; daily ledgers
// compare type plus contents (capKeySnapshot), never DUMP bytes.

import (
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// A fence record is durable evidence of unresolved work on every bucket of its
// reservation. Losing only the bucket's uncertainty counter must not reopen
// admission. Adapted from the verifier probe for the repeated Fence: instead
// of silently blessing an ineffective fence, it re-establishes the counter
// from that evidence, and the following admission then mutates nothing.
func TestSolR5MissingFencedUncertaintyNeverAdmits(t *testing.T) {
	for _, repeatFence := range []bool{false, true} {
		t.Run(fmt.Sprintf("repeatFence=%v", repeatFence), func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
				t.Fatal(err)
			}
			if err := rdb.HDel(bg, keys[0], "uncertain:agent:a").Err(); err != nil {
				t.Fatal(err)
			}
			if repeatFence {
				if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
					t.Fatalf("repeat fence refused: %v", err)
				}
				if got := rdb.HGet(bg, keys[0], "uncertain:agent:a").Val(); got != "1" {
					t.Fatalf("repeat fence did not re-establish the lost counter from its fence record: %q", got)
				}
			}
			before := solDailySnapshot(t, s)
			res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
			t.Logf("reserve=%+v err=%v ledger=%v fences=%v holds=%v", res, err, rdb.HGetAll(bg, keys[0]).Val(), rdb.HGetAll(bg, keys[5]).Val(), rdb.HGetAll(bg, keys[1]).Val())
			if err == nil && res.Status == "ok" {
				t.Error("missing uncertainty counter reopened a durably fenced bucket")
			}
			if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
				t.Error("admission mutated a ledger with missing uncertainty state")
			}
		})
	}
}

func TestSolR5ActivationValidationBeforeExpiryFencing(t *testing.T) {
	for _, activation := range []string{"", "not-a-date", "2026-10-27T00:00:00.5Z"} {
		t.Run(fmt.Sprintf("activation=%q", activation), func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := rdb.HSet(bg, keys[4], "activation:agent:a", activation).Err(); err != nil {
				t.Fatal(err)
			}
			if err := rdb.ZAdd(bg, keys[2], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
				t.Fatal(err)
			}
			before := solDailySnapshot(t, s)
			res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
			after := solDailySnapshot(t, s)
			t.Logf("reserve=%+v err=%v changed=%v expiry=%v fences=%v", res, err, !reflect.DeepEqual(before, after), rdb.ZRangeWithScores(bg, keys[2], 0, -1).Val(), rdb.HGetAll(bg, keys[5]).Val())
			if err == nil && res.Status == "ok" {
				t.Error("malformed activation accepted")
			}
			if !reflect.DeepEqual(before, after) {
				t.Error("activation refusal happened after expiry fencing mutated ledger")
			}
		})
	}
}

func TestSolR5SecondBucketPreflightNeverMutates(t *testing.T) {
	for _, op := range []string{"reserve", "reserve-expired", "complete", "fence"} {
		t.Run(op, func(t *testing.T) {
			s := capTestStore(t)
			b := activatedBuckets(t, "agent:a", 50000, 4000)
			b = append(b, activatedBuckets(t, "key:b", 50000, 4000)...)
			res, err := s.Reserve(bg, "g8-org", round4Day, "first", 1, b, time.Now().Add(time.Minute))
			if err != nil || res.Status != "ok" {
				t.Fatalf("control=%+v %v", res, err)
			}
			keys, _ := s.keys("g8-org", round4Day)
			rdb := s.store.client.Redis()
			if op == "reserve-expired" {
				if err := rdb.ZAdd(bg, keys[2], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			field := "spent:key:b"
			if op == "fence" {
				field = "uncertain:key:b"
			}
			if err := rdb.HSet(bg, keys[0], field, "1.0").Err(); err != nil {
				t.Fatal(err)
			}
			before := solDailySnapshot(t, s)
			switch op {
			case "reserve", "reserve-expired":
				res, err = s.Reserve(bg, "g8-org", round4Day, "next", 1, b, time.Now().Add(time.Minute))
				if err == nil && res.Status == "ok" {
					t.Error("malformed second bucket admitted")
				}
			case "complete":
				err = s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}, {Level: "key:b", Amount: 1000}})
				if err == nil {
					t.Error("malformed second bucket completed")
				}
			case "fence":
				err = s.Fence(bg, "g8-org", round4Day, "first")
				if err == nil {
					t.Error("malformed second bucket fenced")
				}
			}
			if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
				t.Errorf("second bucket validation happened after mutation: %v", err)
			}
		})
	}
}

// A malformed numeric counter must not prevent fencing an intact strict hold.
// Once only the malformed counter is repaired, the pending reservation must
// still fence new work until its authoritative Complete succeeds.
func TestSolR5StrictRefusedCompleteRemainsFenceable(t *testing.T) {
	for _, raw := range []string{"4000.0", "4e3", "-0", "00"} {
		t.Run(fmt.Sprintf("spent=%q", raw), func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "sol-r5-strict-fence"
			res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour)
			if err != nil || res.Status != AgentBudgetOK {
				t.Fatalf("control=%+v %v", res, err)
			}
			keys := s.strictKeys("g8-org", scope)
			rdb := s.client.Redis()
			if err := rdb.Set(bg, keys[1], raw, 0).Err(); err != nil {
				t.Fatal(err)
			}
			before := strictLedgerSnapshot(t, s, scope)
			_, completeErr := s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour)
			if completeErr == nil {
				t.Fatal("malformed spent counter accepted")
			}
			if !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
				t.Error("refused completion mutated ledger")
			}
			fenceErr := s.FenceStrict(bg, "g8-org", scope, "first")
			t.Logf("complete=%v fence=%v available=%v hold=%q uncertainty=%v expiry=%v", completeErr, fenceErr, s.client.Available(), rdb.HGet(bg, keys[3], "first").Val(), rdb.SMembers(bg, keys[8]).Val(), rdb.ZRangeWithScores(bg, keys[4], 0, -1).Val())
			if fenceErr != nil {
				t.Error("intact refused reservation cannot be fenced while spent is malformed")
			}
			if err := rdb.Set(bg, keys[1], "0", 0).Err(); err != nil {
				t.Fatal(err)
			}
			res, err = s.ReserveStrict(bg, "g8-org", scope, "next", 40000, 50000, time.Hour, time.Hour)
			t.Logf("after counter-only repair: reserve=%+v err=%v holds=%v uncertainty=%v terminal=%v", res, err, rdb.HGetAll(bg, keys[3]).Val(), rdb.SMembers(bg, keys[8]).Val(), rdb.HGetAll(bg, keys[6]).Val())
			if err == nil && res.Status == AgentBudgetOK {
				t.Error("counter-only repair reopened unaccounted strict work")
			}
		})
	}
}

// key[5] (stopped) holds any string reason. A stopped scope refuses new work,
// but must still settle its existing hold.
func TestSolR5StoppedStringIsAValidMarker(t *testing.T) {
	s := capTestStore(t).store
	const scope = "sol-r5-stop-control"
	res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour)
	if err != nil || res.Status != AgentBudgetOK {
		t.Fatalf("control=%+v %v", res, err)
	}
	keys := s.strictKeys("g8-org", scope)
	rdb := s.client.Redis()
	if err := rdb.Set(bg, keys[5], "4000.0", 0).Err(); err != nil {
		t.Fatal(err)
	}
	res, err = s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 50000, time.Hour, time.Hour)
	if err != nil || res.Status != AgentBudgetStopped || !res.Stopped || res.StopReason != "4000.0" {
		t.Fatalf("valid stop marker must report stopped: %+v %v", res, err)
	}
	if rdb.HLen(bg, keys[3]).Val() != 1 || rdb.HExists(bg, keys[3], "next").Val() {
		t.Fatal("stopped scope admitted new work")
	}
	res, err = s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour)
	if err != nil || res.SpentMicros != 1000 || res.HeldMicros != 0 || !res.Stopped || res.StopReason != "4000.0" {
		t.Fatalf("valid stop reason must not prevent settling existing work: %+v %v", res, err)
	}
}

// FenceStrict reads no counter: with limit, spent or held malformed it still
// fences the intact hold, leaves every counter as it was, and replies with a
// bare acknowledgement (a state reply would have to parse those counters).
// After a counter-only repair the scope stays uncertain until the
// authoritative Complete settles the actual exactly once.
func TestStrictFenceNeverReadsCounters(t *testing.T) {
	for key, name := range []string{"limit", "spent", "held"} {
		for _, raw := range []string{"4000.0", "4e3", "-0", "00", "nan", "-2", ""} {
			t.Run(fmt.Sprintf("%s=%q", name, raw), func(t *testing.T) {
				s := capTestStore(t).store
				const scope = "fence-no-counters"
				if res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour); err != nil || res.Status != AgentBudgetOK {
					t.Fatalf("control=%+v %v", res, err)
				}
				keys := s.strictKeys("g8-org", scope)
				rdb := s.client.Redis()
				original := rdb.Get(bg, keys[key]).Val()
				if err := rdb.Set(bg, keys[key], raw, 0).Err(); err != nil {
					t.Fatal(err)
				}
				counters := func() [3]string {
					return [3]string{rdb.Get(bg, keys[0]).Val(), rdb.Get(bg, keys[1]).Val(), rdb.Get(bg, keys[2]).Val()}
				}
				before := counters()
				if _, err := s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour); err == nil {
					t.Fatal("malformed counter accepted by completion")
				}
				if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
					t.Fatalf("intact hold not fenced while %s is malformed: %v", name, err)
				}
				if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
					t.Fatalf("repeated fence refused: %v", err)
				}
				if counters() != before || rdb.HGet(bg, keys[3], "first").Val() != "4000" {
					t.Fatalf("fence changed counters or the hold: before=%v after=%v hold=%q", before, counters(), rdb.HGet(bg, keys[3], "first").Val())
				}
				if got := rdb.SMembers(bg, keys[8]).Val(); !reflect.DeepEqual(got, []string{"first"}) || rdb.ZScore(bg, keys[4], "first").Err() != redis.Nil {
					t.Fatalf("fence did not mark the hold uncertain: uncertainty=%v expiry=%v", got, rdb.ZRangeWithScores(bg, keys[4], 0, -1).Val())
				}
				if err := s.FenceStrict(bg, "g8-org", scope, "absent"); err == nil {
					t.Fatal("fence accepted an unknown reservation")
				}
				if err := rdb.Set(bg, keys[key], original, 0).Err(); err != nil {
					t.Fatal(err)
				}
				res, err := s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 50000, time.Hour, time.Hour)
				if err == nil && res.Status == AgentBudgetOK {
					t.Fatalf("counter-only repair reopened the fenced scope: %+v", res)
				}
				if rdb.HExists(bg, keys[3], "next").Val() {
					t.Fatal("fenced scope recorded a new hold")
				}
				res, err = s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour)
				if err != nil || res.SpentMicros != 30000 || res.HeldMicros != 0 || rdb.SCard(bg, keys[8]).Val() != 0 {
					t.Fatalf("authoritative completion after repair: %+v %v uncertainty=%v", res, err, rdb.SMembers(bg, keys[8]).Val())
				}
				if res, err := s.ReserveStrict(bg, "g8-org", scope, "after", 4000, 50000, time.Hour, time.Hour); err != nil || res.Status != AgentBudgetOK {
					t.Fatalf("resolved scope refused valid work: %+v %v", res, err)
				}
			})
		}
	}
}

// A wrong-type limit, spent, held, stop reason or adoption marker: Complete
// refuses, Fence still marks the hold uncertain, and once only that key is
// repaired the scope stays closed until the authoritative Complete.
func TestStrictFenceIgnoresKeysItNeverTouches(t *testing.T) {
	for _, c := range []struct {
		key  int
		name string
	}{{0, "limit"}, {1, "spent"}, {2, "held"}, {5, "stopped"}, {7, "marker"}} {
		t.Run(c.name, func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "fence-untouched-keys"
			if res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour); err != nil || res.Status != AgentBudgetOK {
				t.Fatalf("control=%+v %v", res, err)
			}
			keys := s.strictKeys("g8-org", scope)
			rdb := s.client.Redis()
			original, getErr := rdb.Get(bg, keys[c.key]).Result()
			if getErr != nil && getErr != redis.Nil {
				t.Fatal(getErr)
			}
			if err := rdb.Del(bg, keys[c.key]).Err(); err != nil {
				t.Fatal(err)
			}
			if err := rdb.HSet(bg, keys[c.key], "f", "1").Err(); err != nil {
				t.Fatal(err)
			}
			if _, err := s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour); err == nil {
				t.Fatal("completion accepted a wrong-type key")
			}
			if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
				t.Fatalf("hold not fenced while %s has the wrong type: %v", c.name, err)
			}
			if err := rdb.Del(bg, keys[c.key]).Err(); err != nil {
				t.Fatal(err)
			}
			if getErr == nil {
				if err := rdb.Set(bg, keys[c.key], original, 0).Err(); err != nil {
					t.Fatal(err)
				}
			}
			res, err := s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 50000, time.Hour, time.Hour)
			if err == nil && res.Status == AgentBudgetOK {
				t.Fatalf("type-only repair reopened the fenced scope: %+v", res)
			}
			res, err = s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour)
			if err != nil || res.SpentMicros != 30000 || res.HeldMicros != 0 || rdb.SCard(bg, keys[8]).Val() != 0 {
				t.Fatalf("authoritative completion after repair: %+v %v uncertainty=%v", res, err, rdb.SMembers(bg, keys[8]).Val())
			}
		})
	}
}

// A strict hold whose adoption marker was lost is still fenced: the next
// reservation adopts the scope, sees the uncertainty and refuses, rather than
// admitting beside an unresolved actual.
func TestStrictFenceSurvivesLostAdoptionMarker(t *testing.T) {
	s := capTestStore(t).store
	const scope = "fence-lost-marker"
	if res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Hour, time.Hour); err != nil || res.Status != AgentBudgetOK {
		t.Fatalf("control=%+v %v", res, err)
	}
	keys := s.strictKeys("g8-org", scope)
	rdb := s.client.Redis()
	if err := rdb.Del(bg, keys[7]).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Complete(bg, "g8-org", scope, "first", 30000, time.Hour); err == nil {
		t.Fatal("completion accepted a scope without its adoption marker")
	}
	if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
		t.Fatalf("hold not fenced after its marker was lost: %v", err)
	}
	res, err := s.ReserveStrict(bg, "g8-org", scope, "next", 40000, 50000, time.Hour, time.Hour)
	if err == nil && res.Status == AgentBudgetOK {
		t.Fatalf("lost marker reopened unaccounted strict work: %+v", res)
	}
	if rdb.HExists(bg, keys[3], "next").Val() {
		t.Fatal("new hold recorded beside an unresolved actual")
	}
}

// Fence recounts its bucket's uncertainty from every retained fence record.
// With two unresolved reservations on one bucket and the counter lost, zeroed
// or under-counted, the bucket stays closed until both settle.
func TestCapFenceRecountsLostUncertainty(t *testing.T) {
	for _, counter := range []string{"missing", "0", "1"} {
		t.Run(counter, func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			b := activatedBuckets(t, "agent:a", 50000, 4000)
			if res, err := s.Reserve(bg, "g8-org", round4Day, "second", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
				t.Fatalf("control=%+v %v", res, err)
			}
			for _, nonce := range []string{"first", "second"} {
				if err := s.Fence(bg, "g8-org", round4Day, nonce); err != nil {
					t.Fatal(err)
				}
			}
			if got := rdb.HGet(bg, keys[0], "uncertain:agent:a").Val(); got != "2" {
				t.Fatalf("control uncertainty=%q", got)
			}
			var err error
			if counter == "missing" {
				err = rdb.HDel(bg, keys[0], "uncertain:agent:a").Err()
			} else {
				err = rdb.HSet(bg, keys[0], "uncertain:agent:a", counter).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before := solDailySnapshot(t, s)
			if res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "unavailable" {
				t.Fatalf("lost uncertainty admitted beside two fence records: %+v %v", res, err)
			}
			if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
				t.Fatal("refused admission mutated the ledger")
			}
			if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
				t.Fatal(err)
			}
			if got := rdb.HGet(bg, keys[0], "uncertain:agent:a").Val(); got != "2" {
				t.Fatalf("repeat fence did not recount both fence records: %q", got)
			}
			settle := func(nonce string, wantUncertain string, wantFences int64) {
				t.Helper()
				if err := s.Complete(bg, "g8-org", round4Day, nonce, []CapBucket{{Level: "agent:a", Amount: 1000}}); err != nil {
					t.Fatalf("complete %s: %v", nonce, err)
				}
				if got := rdb.HGet(bg, keys[0], "uncertain:agent:a").Val(); got != wantUncertain || rdb.HLen(bg, keys[5]).Val() != wantFences {
					t.Fatalf("after %s: uncertainty=%q fences=%v", nonce, got, rdb.HGetAll(bg, keys[5]).Val())
				}
			}
			settle("first", "1", 1)
			if res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "unavailable" {
				t.Fatalf("one unresolved reservation remains, yet admitted: %+v %v", res, err)
			}
			settle("second", "0", 0)
			if res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
				t.Fatalf("resolved bucket refused valid work: %+v %v", res, err)
			}
			if got := rdb.HGet(bg, keys[0], "spent:agent:a").Val(); got != "2000" {
				t.Fatalf("each actual must settle exactly once: spent=%q", got)
			}
		})
	}
}

// The product flow after a lost counter: the fenced Complete is refused, the
// error-path Fence re-establishes the counter, and the retried Complete
// settles the actual exactly once and lifts the fence.
func TestCapFencedCompleteSelfHealsLostUncertainty(t *testing.T) {
	s := capTestStore(t)
	keys := round4DailyReservation(t, s)
	rdb := s.store.client.Redis()
	if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
		t.Fatal(err)
	}
	if err := rdb.HDel(bg, keys[0], "uncertain:agent:a").Err(); err != nil {
		t.Fatal(err)
	}
	actual := []CapBucket{{Level: "agent:a", Amount: 1000}}
	before := solDailySnapshot(t, s)
	if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err == nil {
		t.Fatal("fenced completion accepted without its uncertainty counter")
	}
	if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
		t.Fatal("refused completion mutated the ledger")
	}
	if err := s.Fence(bg, "g8-org", round4Day, "first"); err != nil {
		t.Fatal(err)
	}
	if err := s.Complete(bg, "g8-org", round4Day, "first", actual); err != nil {
		t.Fatalf("completion after the counter was re-established: %v", err)
	}
	state := rdb.HGetAll(bg, keys[0]).Val()
	if state["spent:agent:a"] != "1000" || state["held:agent:a"] != "0" || state["uncertain:agent:a"] != "0" || rdb.HLen(bg, keys[1]).Val() != 0 || rdb.HLen(bg, keys[3]).Val() != 1 || rdb.HLen(bg, keys[5]).Val() != 0 {
		t.Fatalf("late settlement did not resolve exactly once: state=%v holds=%d terminal=%d fences=%d", state, rdb.HLen(bg, keys[1]).Val(), rdb.HLen(bg, keys[3]).Val(), rdb.HLen(bg, keys[5]).Val())
	}
	if res, err := s.Reserve(bg, "g8-org", round4Day, "after", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
		t.Fatalf("resolved ledger refused valid work: %+v %v", res, err)
	}
}

// Fence evidence that cannot be read (a fence without its reservation, an
// undecodable reservation, a noncanonical fence value) may cover any bucket,
// so a zero or missing counter beside it is refused without any write.
func TestCapFenceEvidenceIntegrityRefusesAdmission(t *testing.T) {
	for _, scenario := range []string{"fence-without-reservation", "reservation-undecodable", "fence-value-noncanonical"} {
		t.Run(scenario, func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}}); err != nil {
				t.Fatal(err)
			}
			var err error
			switch scenario {
			case "fence-without-reservation":
				err = rdb.HSet(bg, keys[5], "ghost", "1").Err()
			case "reservation-undecodable":
				if err = rdb.HSet(bg, keys[1], "ghost", "{").Err(); err == nil {
					err = rdb.HSet(bg, keys[5], "ghost", "1").Err()
				}
			case "fence-value-noncanonical":
				res, e := s.Reserve(bg, "g8-org", round4Day, "other", 1, activatedBuckets(t, "key:b", 50000, 4000), time.Now().Add(time.Minute))
				if e != nil || res.Status != "ok" {
					t.Fatalf("control=%+v %v", res, e)
				}
				if err = s.Fence(bg, "g8-org", round4Day, "other"); err == nil {
					err = rdb.HSet(bg, keys[5], "other", "1.0").Err()
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			before := solDailySnapshot(t, s)
			res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
			if err == nil && res.Status == "ok" {
				t.Errorf("unreadable fence evidence admitted a bucket with no uncertainty counter: %+v", res)
			}
			if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
				t.Fatal("refused admission mutated the ledger")
			}
		})
	}
}

// Liveness control: a readable fence on one bucket never blocks another.
func TestCapFenceOnOtherBucketDoesNotBlock(t *testing.T) {
	s := capTestStore(t)
	keys := round4DailyReservation(t, s)
	rdb := s.store.client.Redis()
	if err := s.Complete(bg, "g8-org", round4Day, "first", []CapBucket{{Level: "agent:a", Amount: 1000}}); err != nil {
		t.Fatal(err)
	}
	if res, err := s.Reserve(bg, "g8-org", round4Day, "other", 1, activatedBuckets(t, "key:b", 50000, 4000), time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
		t.Fatalf("control=%+v %v", res, err)
	}
	if err := s.Fence(bg, "g8-org", round4Day, "other"); err != nil {
		t.Fatal(err)
	}
	if res, err := s.Reserve(bg, "g8-org", round4Day, "blocked", 1, activatedBuckets(t, "key:b", 50000, 1), time.Now().Add(time.Minute)); err != nil || res.Status != "unavailable" {
		t.Fatalf("fenced bucket admitted: %+v %v", res, err)
	}
	if res, err := s.Reserve(bg, "g8-org", round4Day, "next", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
		t.Fatalf("another bucket's fence blocked a clear bucket: %+v %v state=%v", res, err, rdb.HGetAll(bg, keys[0]).Val())
	}
}

// Activation identity is checked before the expiry sweep and before the
// nonce-replay fast path. A stale caller still gets 'stale' first.
func TestCapActivationMismatchRefusesBeforeSweepAndReplay(t *testing.T) {
	for _, activation := range []string{"", "not-a-date", "2026-10-26T00:00:00Z"} {
		t.Run(fmt.Sprintf("replay/activation=%q", activation), func(t *testing.T) {
			s := capTestStore(t)
			keys := round4DailyReservation(t, s)
			rdb := s.store.client.Redis()
			if err := rdb.HSet(bg, keys[4], "activation:agent:a", activation).Err(); err != nil {
				t.Fatal(err)
			}
			if err := rdb.ZAdd(bg, keys[2], redis.Z{Score: 0, Member: "first"}).Err(); err != nil {
				t.Fatal(err)
			}
			before := solDailySnapshot(t, s)
			res, err := s.Reserve(bg, "g8-org", round4Day, "first", 1, activatedBuckets(t, "agent:a", 50000, 4000), time.Now().Add(time.Minute))
			if err != nil || res.Status != "unavailable" {
				t.Errorf("mismatched activation on replay: %+v %v", res, err)
			}
			if !reflect.DeepEqual(before, solDailySnapshot(t, s)) {
				t.Error("replay refusal happened after expiry fencing mutated the ledger")
			}
		})
	}
	t.Run("stale-precedence", func(t *testing.T) {
		s := capTestStore(t)
		b := activatedBuckets(t, "agent:a", 50000, 4000)
		if res, err := s.Reserve(bg, "g8-org", round4Day, "first", 2, b, time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
			t.Fatalf("control=%+v %v", res, err)
		}
		keys, _ := s.keys("g8-org", round4Day)
		if err := s.store.client.Redis().HSet(bg, keys[4], "activation:agent:a", "2026-10-26T00:00:00Z").Err(); err != nil {
			t.Fatal(err)
		}
		if res, err := s.Reserve(bg, "g8-org", round4Day, "old", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "stale" {
			t.Fatalf("stale caller must be told stale before activation: %+v %v", res, err)
		}
	})
}
