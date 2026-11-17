package redisstate

import (
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Redis scripts do not roll back writes on error. Refusal must preserve the
// complete ledger, including the hold, expiry index and adoption marker.
func strictLedgerSnapshot(t *testing.T, s *AgentBudgetStore, scope string) []string {
	t.Helper()
	var snapshot []string
	for _, key := range s.strictKeys("g8-org", scope) {
		value, err := s.client.Redis().Dump(bg, key).Result()
		if err != nil && err != redis.Nil {
			t.Fatal(err)
		}
		snapshot = append(snapshot, value)
	}
	return snapshot
}

func TestStrictRunMalformedCountersNeverMutate(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for key := 0; key < 3; key++ {
			for _, raw := range []string{"4000.0", "4e3", " 4000", "0x0FA0", "4000.5", "1e-9", "nan", "inf", "-4000", "-0", "00", "+1", "", "9007199254740992"} {
				t.Run(fmt.Sprintf("legacy=%v/key=%d/%q", legacy, key, raw), func(t *testing.T) {
					s := capTestStore(t).store
					const scope = "corrupt-counters"
					var err error
					if legacy {
						_, err = s.Reserve(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
					} else {
						_, err = s.ReserveStrict(bg, "g8-org", scope, "first", 4000, 50000, time.Minute, time.Hour)
					}
					if err != nil {
						t.Fatal(err)
					}
					if err := s.client.Redis().Set(bg, s.keys("g8-org", scope)[key], raw, 0).Err(); err != nil {
						t.Fatal(err)
					}
					before := strictLedgerSnapshot(t, s, scope)
					if _, err := s.PeekStrict(bg, "g8-org", scope); err == nil {
						t.Error("peek accepted a malformed counter")
					}
					for _, nonce := range []string{"next", "first"} {
						if _, err := s.ReserveStrict(bg, "g8-org", scope, nonce, 4000, 40000, time.Minute, time.Hour); err == nil {
							t.Error("reserve/adoption accepted a malformed counter")
						}
						if after := strictLedgerSnapshot(t, s, scope); !reflect.DeepEqual(before, after) {
							t.Fatal("reserve repaired, adopted or partially mutated malformed state")
						}
					}
					if _, err := s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour); err == nil {
						t.Error("complete accepted a malformed counter")
					}
					if after := strictLedgerSnapshot(t, s, scope); !reflect.DeepEqual(before, after) {
						t.Fatal("complete released a hold or changed malformed state")
					}
				})
			}
		}
	}
}

func TestStrictRunPreflightBeforeCounterWrites(t *testing.T) {
	for _, scenario := range []string{"held-overflow", "spent-overflow", "hold-fraction", "hold-negative", "hold-exceeds-held"} {
		t.Run(scenario, func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "preflight"
			if _, err := s.ReserveStrict(bg, "g8-org", scope, "first", 4000, -1, time.Minute, time.Hour); err != nil {
				t.Fatal(err)
			}
			keys := s.strictKeys("g8-org", scope)
			rdb := s.client.Redis()
			var err error
			switch scenario {
			case "held-overflow":
				err = rdb.Set(bg, keys[2], "9007199254740991", 0).Err()
			case "spent-overflow":
				err = rdb.Set(bg, keys[1], "9007199254740991", 0).Err()
			case "hold-fraction":
				err = rdb.HSet(bg, keys[3], "first", "4000.5").Err()
			case "hold-negative":
				err = rdb.HSet(bg, keys[3], "first", "-4000").Err()
			case "hold-exceeds-held":
				err = rdb.Set(bg, keys[2], "3999", 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before := strictLedgerSnapshot(t, s, scope)
			if scenario == "held-overflow" {
				_, err = s.ReserveStrict(bg, "g8-org", scope, "next", 1, -1, time.Minute, time.Hour)
			} else {
				_, err = s.Complete(bg, "g8-org", scope, "first", 1, time.Hour)
			}
			if err == nil {
				t.Error("unsafe counter write accepted")
			}
			if !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
				t.Fatal("failed preflight partially mutated the ledger")
			}
		})
	}
}

func TestStrictRunCanonicalLegacyAdoption(t *testing.T) {
	for _, limit := range []int64{-1, 0, 50000} {
		t.Run(fmt.Sprint(limit), func(t *testing.T) {
			s := capTestStore(t).store
			const scope = "legacy-valid"
			keys := s.keys("g8-org", scope)
			for i, value := range []int64{limit, 0, 0} {
				if err := s.client.Redis().Set(bg, keys[i], value, time.Hour).Err(); err != nil {
					t.Fatal(err)
				}
			}
			res, err := s.ReserveStrict(bg, "g8-org", scope, "zero", 0, limit, time.Minute, time.Hour)
			if err != nil || res.Status != AgentBudgetOK || res.LimitMicros != limit {
				t.Fatalf("valid legacy adoption = %+v, %v", res, err)
			}
			if _, err := s.Complete(bg, "g8-org", scope, "zero", 0, time.Hour); err != nil {
				t.Fatal(err)
			}
			for _, key := range s.strictKeys("g8-org", scope) {
				if ttl := s.client.Redis().PTTL(bg, key).Val(); ttl > 0 {
					t.Fatalf("adopted strict state expires: %s %v", key, ttl)
				}
			}
		})
	}
}
