package redisstate

import (
	"fmt"
	"reflect"
	"testing"
	"time"
)

// Holds partition into expiring reservations and fenced uncertainty. A missing
// index must refuse admission without rewriting the ledger, even for a zero hold.
func TestStrictOrphanHoldRefusesPeekAndReserve(t *testing.T) {
	for _, amount := range []int64{0, 4000} {
		for _, missing := range []string{"uncertainty", "expiry"} {
			t.Run(fmt.Sprintf("%s/%d", missing, amount), func(t *testing.T) {
				s := capTestStore(t).store
				const scope = "orphan-index"
				res, err := s.ReserveStrict(bg, "g8-org", scope, "first", amount, 50000, time.Hour, time.Hour)
				if err != nil || res.Status != AgentBudgetOK {
					t.Fatalf("reserve control: %+v %v", res, err)
				}
				keys := s.strictKeys("g8-org", scope)
				index := keys[4]
				if missing == "uncertainty" {
					if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
						t.Fatal(err)
					}
					index = keys[8]
				}
				if err := s.client.Redis().Del(bg, index).Err(); err != nil {
					t.Fatal(err)
				}
				before := strictLedgerSnapshot(t, s, scope)
				res, err = s.PeekStrict(bg, "g8-org", scope)
				if err != nil || res.Status != "uncertain" {
					t.Errorf("peek must report orphan uncertainty: %+v %v", res, err)
				}
				for _, nonce := range []string{"first", "next"} {
					// A lower limit exposes policy writes before preflight; replays
					// and fresh reservations must both leave all state untouched.
					res, err = s.ReserveStrict(bg, "g8-org", scope, nonce, amount, 45000, time.Hour, time.Hour)
					if err != nil || res.Status != "uncertain" {
						t.Errorf("reserve %s must report orphan uncertainty: %+v %v", nonce, res, err)
					}
					if !reflect.DeepEqual(before, strictLedgerSnapshot(t, s, scope)) {
						t.Error("orphan refusal mutated ledger")
					}
				}
				if !s.client.Available() {
					t.Fatal("semantic uncertainty refusal opened the shared Redis breaker")
				}
				// Authoritative settlement remains a recovery path. Admission
				// must not require reconstructing an operationally lost index.
				if _, err := s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour); err != nil {
					t.Fatal(err)
				}
				res, err = s.ReserveStrict(bg, "g8-org", scope, "after", 4000, 50000, time.Hour, time.Hour)
				if err != nil || res.Status != AgentBudgetOK || res.SpentMicros != 1000 {
					t.Fatalf("settlement did not recover scope: %+v %v", res, err)
				}
			})
		}
	}
}

func TestStrictPendingIndexLifecycle(t *testing.T) {
	s := capTestStore(t).store
	const scope = "pending-index-control"
	for _, nonce := range []string{"first", "second"} {
		res, err := s.ReserveStrict(bg, "g8-org", scope, nonce, 4000, 50000, time.Hour, time.Hour)
		if err != nil || res.Status != AgentBudgetOK {
			t.Fatalf("active holds refused: %+v %v", res, err)
		}
	}
	if err := s.FenceStrict(bg, "g8-org", scope, "first"); err != nil {
		t.Fatal(err)
	}
	res, err := s.ReserveStrict(bg, "g8-org", scope, "next", 4000, 50000, time.Hour, time.Hour)
	if err != nil || res.Status != "uncertain" {
		t.Fatalf("mixed active/fenced holds: %+v %v", res, err)
	}
	if _, err := s.Complete(bg, "g8-org", scope, "first", 1000, time.Hour); err != nil {
		t.Fatal(err)
	}
	res, err = s.PeekStrict(bg, "g8-org", scope)
	if err != nil || res.Status != AgentBudgetOK || res.HeldMicros != 4000 {
		t.Fatalf("remaining active hold refused: %+v %v", res, err)
	}
}
