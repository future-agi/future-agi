package redisstate

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestMixedOwnershipMigratesBeforeDispatchAndRetainsLiability(t *testing.T) {
	for _, known := range []bool{false, true} {
		t.Run(map[bool]string{false: "unresolved", true: "known"}[known], func(t *testing.T) {
			s, rdb, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 10000, 1, 0)
			if _, err := s.Reserve(bg, "org-owned", "mixed", 100, time.Hour, billing.ReserveNew); err != nil {
				t.Fatal(err)
			}
			prior := billing.OwnedAttempt{Model: "synthetic/token", Micros: 100, Dispatched: true, TokenPriced: true}
			if known {
				prior.Summary = &billing.AttemptSummary{AttemptID: "mixed.1", Model: prior.Model, Provider: "openrouter", CredentialType: "managed", BilledMicros: 53, UpstreamMicros: 50, Outcome: "error"}
			}
			rec := billing.Settlement{OrgID: "org-owned", RequestID: "mixed", Provider: "openrouter", ManagedJournal: map[string]billing.OwnedAttempt{"mixed.1": prior}}
			if _, err := s.ReserveOwned(bg, "org-owned", "mixed", "mixed.2", "google/gemini-3.5-flash", 200, time.Hour, billing.ReserveAdd, false, rec); err != nil {
				t.Fatal(err)
			}
			// Crash BEFORE the inclusive fallback dispatch. Its hold is released, but
			// the prior dispatched token liability is already durable and cannot expire.
			restarted := NewManagedCreditStore(s.client, s.prefix)
			restarted.now = func() time.Time { return time.Now().Add(25 * time.Hour) }
			if _, err := restarted.RecoverInclusive(bg, "org-owned", "mixed"); err != nil {
				t.Fatal(err)
			}
			want := int64(100)
			if known {
				want = 53
			}
			acct := mustAccount(t, s, "org-owned")
			rows, _ := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
			if acct.HeldMicros != 0 || acct.SpentMicros != want || len(rows) != 1 {
				t.Fatalf("account=%+v rows=%v want=%d", acct, rows, want)
			}
			if _, err := s.Reserve(bg, "org-owned", "mixed", 1, time.Hour, billing.ReserveAdd); err == nil {
				t.Fatal("legacy reserve bypassed ownership")
			}
		})
	}
}

// The database proof consumes these actual Go/Redis v1 outbox rows, not a
// Python approximation of Fields(). An optional export also powers the gate.
func TestMixedDjangoOutbox(t *testing.T) {
	fixtures := map[string]map[string]any{}
	for _, name := range []string{"single", "mixed", "estimate", "overhold"} {
		t.Run(name, func(t *testing.T) {
			s, rdb, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 10000, 1, 0)
			id := "cat3-" + name
			if _, err := s.ReserveInclusive(bg, "org-owned", id, id+".1", "google/gemini-3.5-flash", 100, time.Hour, billing.ReserveNew); err != nil {
				t.Fatal(err)
			}
			if err := s.BeginInclusive(bg, "org-owned", id, id+".1"); err != nil {
				t.Fatal(err)
			}
			billed := int64(11)
			up := int64(10)
			if name == "estimate" {
				billed = 100
				up = 95
			}
			if name == "overhold" {
				billed = 1050
				up = 1000
			}
			rec := ownedRecord(id, billed)
			rec.UpstreamMicros = up
			rec.MarkupMicros = billed - up
			rec.TsMs = 1793102400000
			rec.CatalogSnapshotID = "synthetic-cat3"
			evidence := &rec.Dimensional.InclusiveAttempts[0]
			evidence.UpstreamMicros = up
			rec.Attempts = []billing.AttemptSummary{{AttemptID: id + ".1", Model: evidence.Model, Provider: "openrouter", CredentialType: "managed", Kind: "primary", Outcome: "success", BilledMicros: billed, UpstreamMicros: up}}
			if name == "estimate" {
				rec.UsageEstimated = true
				evidence.Estimated = true
			}
			if name == "overhold" {
				evidence.Anomaly = "provider charge exceeds admitted hold"
			}
			if name == "mixed" {
				if _, err := s.ReserveOwned(bg, "org-owned", id, id+".2", "synthetic/token", 100, time.Hour, billing.ReserveAdd, true, rec); err != nil {
					t.Fatal(err)
				}
				if err := s.BeginInclusiveWithRecord(bg, "org-owned", id, id+".2", rec); err != nil {
					t.Fatal(err)
				}
				rec.BilledMicros += 53
				rec.UpstreamMicros += 50
				rec.MarkupMicros = rec.BilledMicros - rec.UpstreamMicros
				rec.HoldMicros = 200
				rec.Attempts = append(rec.Attempts, billing.AttemptSummary{AttemptID: id + ".2", Model: "synthetic/token", Provider: "openrouter", CredentialType: "managed", Kind: "model_fallback", Outcome: "success", BilledMicros: 53, UpstreamMicros: 50})
			}
			if _, err := s.Settle(bg, "org-owned", id, rec); err != nil {
				t.Fatal(err)
			}
			if res, err := s.Settle(bg, "org-owned", id, rec); err != nil || res.Applied {
				t.Fatalf("replay=%+v %v", res, err)
			}
			rows, err := rdb.XRange(bg, s.keys("org-owned").outbox, "-", "+").Result()
			if err != nil || len(rows) != 1 {
				t.Fatalf("rows=%v %v", rows, err)
			}
			if rows[0].Values["v"] != "1" || rows[0].Values["dimensions"] != nil {
				t.Fatalf("non-v1: %v", rows)
			}
			if name == "overhold" {
				if q, err := s.InclusiveQuarantined(bg, "other-org", evidence.Model); err != nil || !q {
					t.Fatalf("global quarantine=%t %v", q, err)
				}
			}
			fixtures[name] = rows[0].Values
		})
	}
	if t.Failed() {
		return
	}
	data, err := json.MarshalIndent(fixtures, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	data = append(data, '\n')
	golden, err := os.ReadFile("testdata/onekey_outbox.json")
	if err != nil {
		t.Fatal(err)
	}
	// Compare JSON values so repository formatters cannot change the contract.
	var want, got any
	if json.Unmarshal(golden, &want) != nil || json.Unmarshal(data, &got) != nil {
		t.Fatal("invalid outbox fixture")
	}
	canonicalWant, _ := json.Marshal(want)
	canonicalGot, _ := json.Marshal(got)
	if !bytes.Equal(canonicalWant, canonicalGot) {
		t.Fatal("Go outbox diverged from the Django proof fixture")
	}

	if path := os.Getenv("ONEKEY_OUTBOX_FIXTURES"); path != "" {
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
