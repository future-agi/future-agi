package redisstate

import (
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestOwnedBeginCleanupRace(t *testing.T) {
	for n := 0; n < 20; n++ {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			s, _, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 1000, 1, 0)
			if _, err := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); err != nil {
				t.Fatal(err)
			}
			start := make(chan struct{})
			var beginErr, voidErr error
			var wg sync.WaitGroup
			wg.Add(2)
			go func() { defer wg.Done(); <-start; beginErr = s.BeginInclusive(bg, "org-owned", "req", "req.1") }()
			go func() { defer wg.Done(); <-start; _, voidErr = s.Void(bg, "org-owned", "req", ownedRecord("req", 0)) }()
			close(start)
			wg.Wait()
			if (beginErr == nil) == (voidErr == nil) {
				t.Fatalf("exactly one transition must win: begin=%v void=%v", beginErr, voidErr)
			}
			acct := mustAccount(t, s, "org-owned")
			if acct.SpentMicros != 0 {
				t.Fatal("undispatched work spent")
			}
			if beginErr == nil && acct.HeldMicros != 100 {
				t.Fatal("cleanup voided reconciliation owner")
			}
			if voidErr == nil && acct.HeldMicros != 0 {
				t.Fatal("undispatched cleanup leaked hold")
			}
		})
	}
}
func TestOwnedRejectsFinancialEvidenceMismatch(t *testing.T) {
	for _, tc := range []string{"org", "request", "amount", "missing_attempt", "estimated_less_than_hold"} {
		t.Run(tc, func(t *testing.T) {
			s, rdb, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 1000, 1, 0)
			if _, err := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); err != nil {
				t.Fatal(err)
			}
			if err := s.BeginInclusive(bg, "org-owned", "req", "req.1"); err != nil {
				t.Fatal(err)
			}
			rec := ownedRecord("req", 90)
			switch tc {
			case "org":
				rec.OrgID = "another-org"
			case "request":
				rec.RequestID = "different"
			case "amount":
				rec.BilledMicros = -1
			case "missing_attempt":
				rec.Dimensional.InclusiveAttempts = nil
			case "estimated_less_than_hold":
				rec.Dimensional.InclusiveAttempts[0].Estimated = true
			}
			if _, err := s.Settle(bg, "org-owned", "req", rec); err == nil {
				t.Fatal("invalid financial evidence accepted")
			}
			if a := mustAccount(t, s, "org-owned"); a.SpentMicros != 0 || a.HeldMicros != 100 {
				t.Fatalf("rejected spend changed money %+v", a)
			}
			if rdb.XLen(bg, s.keys("org-owned").outbox).Val() != 0 {
				t.Fatal("rejected spend emitted financial event")
			}
		})
	}
}
