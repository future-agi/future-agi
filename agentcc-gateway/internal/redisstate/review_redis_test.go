package redisstate

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestCrashRecoveryRetainsPriorManagedAttempt(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	if r, e := s.Reserve(bg, "org-owned", "req", 100, time.Hour, billing.ReserveNew); e != nil || r.Status != billing.ReserveOK {
		t.Fatalf("legacy hold %v %v", r, e)
	}
	// The first managed call already ran and incurred a known 50-micro charge.
	// A fallback starts an inclusive attempt, transferring the request to recovery.
	if r, e := s.ReserveInclusive(bg, "org-owned", "req", "req.2", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveAdd); e != nil || r.Status != billing.ReserveOK {
		t.Fatalf("inclusive hold %v %v", r, e)
	}
	rec := ownedRecord("req", 50)
	rec.HoldMicros = 200
	rec.Attempts = []billing.AttemptSummary{{AttemptID: "req.1", Model: "synthetic/token", Provider: "openrouter", CredentialType: "managed", BilledMicros: 50, UpstreamMicros: 48, Outcome: "error"}}
	rec.Dimensional = nil
	if e := s.BeginInclusiveWithRecord(bg, "org-owned", "req", "req.2", rec); e != nil {
		t.Fatal(e)
	}
	restarted := NewManagedCreditStore(s.client, s.prefix)
	restarted.now = func() time.Time { return time.Now().Add(25 * time.Hour) }
	if _, e := restarted.RecoverInclusive(bg, "org-owned", "req"); e != nil {
		t.Fatal(e)
	}
	acct := mustAccount(t, s, "org-owned")
	rows, _ := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
	if acct.SpentMicros < 150 || acct.HeldMicros != 0 || len(rows) != 1 {
		t.Fatalf("recovery waived prior managed liability: acct=%+v outbox=%v; want at least 50 prior + 100 inclusive", acct, rows)
	}
}

func TestRefundReplayAfterDone(t *testing.T) {
	s, _, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := s.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
		t.Fatal(e)
	}
	rec := ownedRecord("req", 100)
	rec.Kind = billing.KindSpend
	rec.UsageEstimated = true
	rec.Dimensional.InclusiveAttempts[0].Estimated = true
	if _, e := s.Settle(bg, "org-owned", "req", rec); e != nil {
		t.Fatal(e)
	}
	refunds := &refundMock{amounts: map[string]int64{}}
	if e := s.ReconcileOrg(bg, "org-owned", generationMock{}, refunds); e != nil {
		t.Fatal(e)
	}
	restarted := NewManagedCreditStore(s.client, s.prefix)
	if e := restarted.ReconcileOrg(bg, "org-owned", generationMock{}, refunds); e != nil {
		t.Fatal(e)
	}
	if refunds.amounts["org-owned/req"] != 89 || len(refunds.amounts) != 1 {
		t.Fatalf("refund replay %+v", refunds.amounts)
	}
}
