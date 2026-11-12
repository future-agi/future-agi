package redisstate

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR602AttemptBindingImmutableAtSettlement(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	rec := ownedRecord("req", 100)
	rec.Dimensional.InclusiveAttempts[0].Estimated = true
	original := models.InclusiveIdentity{Provider: "openrouter", Credential: models.CredentialFingerprint("synthetic-original"), Scope: "org-owned", SnapshotModels: `["google/gemini-3.5-flash"]`}
	rec.InclusiveBindings = map[string]models.InclusiveIdentity{"req.1": original}
	if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); e != nil {
		t.Fatal(e)
	}
	if e := s.BeginInclusiveWithRecord(bg, "org-owned", "req", "req.1", rec); e != nil {
		t.Fatal(e)
	}
	changed := original
	changed.Credential = models.CredentialFingerprint("synthetic-replacement")
	rec.InclusiveBindings["req.1"] = changed
	if _, e := s.Settle(bg, "org-owned", "req", rec); e == nil {
		t.Fatal("settlement changed dispatch credential")
	}
	if rdb.XLen(bg, s.keys("org-owned").outbox).Val() != 0 {
		t.Fatal("conflicting evidence emitted spend")
	}
	rec.InclusiveBindings["req.1"] = original
	if _, e := s.Settle(bg, "org-owned", "req", rec); e != nil {
		t.Fatal(e)
	}
}
