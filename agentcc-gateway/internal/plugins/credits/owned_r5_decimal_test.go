package credits

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOwnedR5JournalExactDecimalBoundaries(t *testing.T) {
	p := New(true, nil)
	for n := 0; n < 10000; n++ {
		raw := fmt.Sprintf("0.%013d", int64(n)*10000000+4999999)
		var usage models.Usage
		if err := json.Unmarshal([]byte(`{"cost":`+raw+`}`), &usage); err != nil {
			t.Fatal(err)
		}
		up, billed, err := inclusive.Charge(raw)
		if err != nil {
			t.Fatal(err)
		}
		rc := &models.RequestContext{RequestID: "exact", Metadata: map[string]string{}}
		rc.Billing.OwnedRequest = true
		a := models.Attempt{AttemptID: "exact.1", Model: "synthetic/token", Provider: "openrouter", CredentialType: models.CredentialManaged, Outcome: models.OutcomeError, Usage: &usage}
		a.Pricing = models.NewAttemptPricing(&models.BillingState{HoldMicros: 100000, ReservePrice: &models.PriceCard{Prompt: 1e-6}})
		rc.AddAttempt(a)
		// BYOK usage may be recorded for display, never in the owned journal.
		a.AttemptID, a.CredentialType = "exact.2", models.CredentialBYOK
		rc.AddAttempt(a)
		rec := p.journalOf(rc)
		got := rec.ManagedJournal["exact.1"].Summary
		if len(rec.ManagedJournal) != 1 || got == nil || got.UpstreamMicros != up || got.BilledMicros != billed {
			t.Fatalf("raw=%s want=(%d,%d) journal=%+v entries=%d", raw, up, billed, got, len(rec.ManagedJournal))
		}
	}
}
