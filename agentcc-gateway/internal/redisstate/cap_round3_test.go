package redisstate

// Permanent round-three verifier regressions. Synthetic Redis only.

import (
	"fmt"
	"testing"
	"time"
)

// The strict run ledger is the second cap ledger. Its durable counters must be
// refused when malformed, as the daily ledger's are, instead of being used for
// admission while settlement fails after releasing the hold.
func TestOpusR3StrictRunMalformedCounters(t *testing.T) {
	type target struct {
		key    int
		values []string
	}
	for _, tc := range []target{
		{1, []string{"4000.0", "4e3", " 4000", "0x0FA0", "4000.5", "1e-9", "nan", "-4000"}}, // spent
		{2, []string{"0.0", "0x0", "nan", "-1"}},                                            // held
		{0, []string{"50000.0", "5e4", "nan", "-2"}},                                        // limit
	} {
		for _, value := range tc.values {
			t.Run(fmt.Sprintf("key=%d/%q", tc.key, value), func(t *testing.T) {
				s := capTestStore(t).store
				scope := "malformed-run"
				res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Second)
				if err != nil || res.Status != AgentBudgetOK {
					t.Fatalf("control reserve=%+v %v", res, err)
				}
				if res, err = s.Complete(bg, "g8-org", scope, "first", 4000, time.Second); err != nil || res.SpentMicros != 4000 {
					t.Fatalf("control complete=%+v %v", res, err)
				}
				keys := s.strictKeys("g8-org", scope)
				rdb := s.client.Redis()
				if err := rdb.Set(bg, keys[tc.key], value, 0).Err(); err != nil {
					t.Fatal(err)
				}
				peek, peekErr := s.PeekStrict(bg, "g8-org", scope)
				first, firstErr := s.ReserveStrict(bg, "g8-org", scope, "next-0", 4000, 50000, time.Minute, time.Second)
				admitted, settleErrors := 0, 0
				if firstErr == nil && first.Status == AgentBudgetOK {
					admitted++
					if _, err := s.Complete(bg, "g8-org", scope, "next-0", 4000, time.Second); err != nil {
						settleErrors++
					}
					for i := 1; i < 20; i++ {
						nonce := fmt.Sprintf("next-%d", i)
						r, e := s.ReserveStrict(bg, "g8-org", scope, nonce, 4000, 50000, time.Minute, time.Second)
						if e != nil || r.Status != AgentBudgetOK {
							break
						}
						admitted++
						if _, err := s.Complete(bg, "g8-org", scope, nonce, 4000, time.Second); err != nil {
							settleErrors++
						}
					}
				}
				t.Logf("peek=%+v/%v first=%+v/%v admitted=%d settleErrors=%d limit=%q spent=%q held=%q terminals=%d holds=%d",
					peek, peekErr, first, firstErr, admitted, settleErrors, rdb.Get(bg, keys[0]).Val(), rdb.Get(bg, keys[1]).Val(), rdb.Get(bg, keys[2]).Val(), rdb.HLen(bg, keys[6]).Val(), rdb.HLen(bg, keys[3]).Val())
				// A valid 50000 limit with 4000 already spent admits at most 11 more 4000 holds.
				if admitted > 11 {
					t.Errorf("malformed strict counter admitted unbounded work: admitted=%d settleErrors=%d", admitted, settleErrors)
				}
				if (firstErr == nil && first.Status == AgentBudgetOK) || (peekErr == nil && peek.Status == AgentBudgetOK) {
					t.Errorf("malformed strict state must be refused, not used: peek=%+v/%v reserve=%+v/%v", peek, peekErr, first, firstErr)
				}
			})
		}
	}
}

// Missing strict policy/limit state with the strict marker present is lost
// state, never a fresh scope.
func TestOpusR3StrictRunMissingLimitFailsClosed(t *testing.T) {
	for _, key := range []int{0, 1, 2} {
		t.Run(fmt.Sprint(key), func(t *testing.T) {
			s := capTestStore(t).store
			scope := "missing-limit"
			if res, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Second); err != nil || res.Status != AgentBudgetOK {
				t.Fatalf("control=%+v %v", res, err)
			}
			if _, err := s.Complete(bg, "g8-org", scope, "first", 4000, time.Second); err != nil {
				t.Fatal(err)
			}
			keys := s.strictKeys("g8-org", scope)
			s.client.Redis().Del(bg, keys[key])
			res, err := s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 50000, time.Minute, time.Second)
			if err == nil && res.Status == AgentBudgetOK {
				t.Errorf("missing strict counter treated as fresh: %+v", res)
			}
		})
	}
}

// Daily ledger: malformed values in fields the writer's matrix does not name.
func TestOpusR3DailyMalformedExtraFields(t *testing.T) {
	for _, tc := range []struct {
		key   int
		field string
		value string
	}{
		{0, "spent:agent:a", "-0"}, {0, "spent:agent:a", "00"}, {0, "held:agent:a", "4000.0"},
		{4, "activation:agent:a", ""}, {4, "activation:agent:a", "2026-10-27T00:00:00.5Z"},
	} {
		t.Run(fmt.Sprintf("%d/%s/%q", tc.key, tc.field, tc.value), func(t *testing.T) {
			s := capTestStore(t)
			b := activatedBuckets(t, "agent:a", 50000, 4000)
			if res, err := s.Reserve(bg, "g8-org", "2026-10-27", "first", 1, b, time.Now().Add(time.Minute)); err != nil || res.Status != "ok" {
				t.Fatalf("control=%+v %v", res, err)
			}
			keys, _ := s.keys("g8-org", "2026-10-27")
			if err := s.store.client.Redis().HSet(bg, keys[tc.key], tc.field, tc.value).Err(); err != nil {
				t.Fatal(err)
			}
			res, err := s.Reserve(bg, "g8-org", "2026-10-27", "next", 1, b, time.Now().Add(time.Minute))
			if err == nil && res.Status == "ok" {
				t.Errorf("malformed daily state admitted: %+v", res)
			}
		})
	}
}
