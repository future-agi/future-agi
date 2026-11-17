package redisstate

import (
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestOwnedR4FirstTokenLiabilityExpiresBeforeInclusiveMigration(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	now := time.Now()
	s.now = func() time.Time { return now }
	// The HTTP crash matrix separately asserts this protocol is entered before
	// the first token dispatch. Here expiry and restart exercise its durability.
	// The provider accepts the request, then the process dies before migration to
	// its configured inclusive fallback. No in-memory finalizer can run.
	if res, err := s.ReserveOwned(bg, "org-owned", "mixed-primary", "mixed-primary.1", "synthetic/token", 100, time.Minute, billing.ReserveNew, true, billing.Settlement{}); err != nil || res.Status != billing.ReserveOK {
		t.Fatalf("reserve=%+v %v", res, err)
	}
	if err := s.BeginInclusive(bg, "org-owned", "mixed-primary", "mixed-primary.1"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Reserve(bg, "org-owned", "mixed-primary", 1, time.Hour, billing.ReserveAdd); err == nil {
		t.Fatal("legacy Reserve bypassed ownership")
	}
	now = now.Add(48 * time.Hour)
	if _, err := s.Reserve(bg, "org-owned", "cleanup", 0, time.Hour, billing.ReserveNew); err != nil {
		t.Fatal(err)
	}
	// Recovery must settle the full dispatched hold. With no generation
	// response at all, reconciliation remains pending; that is not a waiver.
	if err := s.ReconcileAll(bg, generationMock{}, &refundMock{amounts: map[string]int64{}}); err == nil || !strings.Contains(err.Error(), "incomplete inclusive reconciliation context") {
		t.Fatalf("missing generation should remain pending: %v", err)
	}
	if pending, err := rdb.SIsMember(bg, s.ownedKeys("org-owned", "")[8], "mixed-primary").Result(); err != nil || !pending {
		t.Fatalf("unresolved liability not retained: %t %v", pending, err)
	}
	acct := mustAccount(t, s, "org-owned")
	rows, err := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != 100 || acct.HeldMicros != 0 || len(rows) != 1 {
		t.Fatalf("dispatched first token hop was silently waived after process loss: acct=%+v outbox=%v", acct, rows)
	}
}
