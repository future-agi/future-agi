package redisstate

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestOwnedR5TerminalSealReplayRetainsFinalJournal(t *testing.T) {
	s, rdb, _ := managedTestStore(t)
	mustGrant(t, s, "org-owned", 10000, 1, 0)
	const req = "terminal-seal"
	const model = "google/gemini-3.5-flash"
	if _, err := s.ReserveInclusive(bg, "org-owned", req, req+".1", model, 100, time.Hour, billing.ReserveNew); err != nil {
		t.Fatal(err)
	}
	if err := s.BeginInclusive(bg, "org-owned", req, req+".1"); err != nil {
		t.Fatal(err)
	}
	before := rdb.Get(bg, s.ownedKeys("org-owned", req)[5]).Val()
	rec := ownedRecord(req, 1050)
	summary := billing.AttemptSummary{AttemptID: req + ".1", Model: model, Provider: "openrouter", CredentialType: "managed", BilledMicros: 1050, UpstreamMicros: 1000, Outcome: "success"}
	rec.Attempts = []billing.AttemptSummary{summary}
	rec.ManagedJournal = map[string]billing.OwnedAttempt{req + ".1": {Model: model, Micros: 100, Dispatched: true, Summary: &summary, Evidence: &rec.Dimensional.InclusiveAttempts[0]}}
	fault := &ownedR4SealFault{key: s.catalogueFenceKey()}
	fault.fail.Store(true)
	rdb.AddHook(fault)
	for i := 0; i < 2; i++ {
		if _, err := s.Settle(bg, "org-owned", req, rec); err == nil {
			t.Fatal("fault did not reject terminal seal")
		}
	}
	raw, err := rdb.Get(bg, s.ownedKeys("org-owned", req)[5]).Bytes()
	if err != nil {
		t.Fatal(err)
	}
	var o billing.OwnedRequest
	if err := json.Unmarshal(raw, &o); err != nil {
		t.Fatal(err)
	}
	if a := o.Attempts[req+".1"]; a.Summary == nil || a.Summary.BilledMicros != 1050 || a.Evidence == nil || a.Evidence.GenerationID != "synthetic-generation" || a.Micros != 100 || o.Record.BilledMicros != 1050 || o.Terminal != "" {
		t.Fatalf("completed record lost before global seal: attempt=%+v record=%+v", a, o.Record)
	}
	if a := mustAccount(t, s, "org-owned"); a.SpentMicros != 0 || a.HeldMicros != 100 || rdb.XLen(bg, s.keys("org-owned").outbox).Val() != 0 {
		t.Fatalf("prepare wrote money before seal: %+v", a)
	}
	fault.fail.Store(false)
	// A stale recovery reader or late admission must not overwrite the final
	// provider response while the seal is retried. The prepared record is the
	// authoritative snapshot, even though no financial event exists yet.
	spend := rec
	spend.Kind = billing.KindSpend
	if _, _, err := s.finishOwned(bg, "org-owned", req, spend, before); err == nil {
		t.Fatal("stale recovery CAS replaced a completed response")
	}
	if _, err := s.ReserveOwned(bg, "org-owned", req, req+".2", "synthetic/other", 100, time.Hour, billing.ReserveAdd, true, billing.Settlement{}); err == nil {
		t.Fatal("new attempt admitted after final settlement preparation")
	}
	empty := billing.Settlement{OrgID: "org-owned", RequestID: req}
	if err := s.BeginInclusiveWithRecord(bg, "org-owned", req, req+".1", empty); err == nil {
		t.Fatal("late Begin replaced a completed response")
	}
	if got := rdb.Get(bg, s.ownedKeys("org-owned", req)[5]).Val(); got != string(raw) {
		t.Fatal("rejected stale writer changed the final journal")
	}
	if res, err := s.Settle(bg, "org-owned", req, rec); err != nil || !res.Applied {
		t.Fatalf("settlement retry: %+v %v", res, err)
	}
	if res, err := s.Settle(bg, "org-owned", req, rec); err != nil || res.Applied {
		t.Fatalf("terminal duplicate: %+v %v", res, err)
	}
	if a := mustAccount(t, s, "org-owned"); a.SpentMicros != 1050 || a.HeldMicros != 0 || rdb.XLen(bg, s.keys("org-owned").outbox).Val() != 1 {
		t.Fatalf("not one terminal spend: %+v", a)
	}
}
