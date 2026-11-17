package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A skipped attempt was never sent, so it never served: servingIndex picks
// the last success, else the last attempt that was sent, and never a skipped
// one, however many follow the attempt that was sent (MIXED-HOP-GUARD-1
// round 2). This file uses only what exists at 2a117d070c, where it is red.
func TestServingIndexNeverSelectsSkipped(t *testing.T) {
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	attempts := []models.Attempt{{Kind: models.AttemptPrimary, Outcome: models.OutcomeError}}
	for range models.MaxAttempts - 1 {
		attempts = append(attempts, models.Attempt{Kind: models.AttemptModelFallback, Outcome: models.OutcomeSkipped,
			Reason: models.ReasonPolicy, CredentialType: models.CredentialBYOK})
	}
	if got := servingIndex(rc, attempts); got != 0 {
		t.Fatalf("servingIndex = %d; want 0, the one attempt that was sent, not a skipped one after it", got)
	}
	attempts = append(attempts[:1], models.Attempt{Kind: models.AttemptModelFallback, Outcome: models.OutcomeSuccess},
		models.Attempt{Kind: models.AttemptModelFallback, Outcome: models.OutcomeSkipped})
	if got := servingIndex(rc, attempts); got != 1 {
		t.Fatalf("servingIndex = %d; want 1, the success", got)
	}
	skippedOnly := []models.Attempt{{Outcome: models.OutcomeSkipped}, {Outcome: models.OutcomeSkipped}}
	if got := servingIndex(rc, skippedOnly); got >= 0 && skippedOnly[got].Outcome == models.OutcomeSkipped {
		t.Fatalf("servingIndex = %d, a skipped attempt", got)
	}
	rc.Response = nil
	if got := servingIndex(rc, attempts); got != -1 {
		t.Fatalf("servingIndex = %d for a request nothing served; want -1", got)
	}
}

// Priced end to end: a served managed request whose recorded attempts are a
// failed primary and skipped entries in the other mode bills the answer on
// the primary, the attempt that was sent, and no skipped entry is billable.
// With every recorded attempt skipped, the answer is priced as the one
// attempt the request made, never on a skipped entry.
func TestFinalizeNeverPricesASkippedAttempt(t *testing.T) {
	card := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourceCatalog}
	for _, c := range []struct {
		name    string
		primary bool
	}{{"failed primary then skipped", true}, {"skipped only", false}} {
		t.Run(c.name, func(t *testing.T) {
			p := billingPlugin(fixedLookup(card))
			rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
			if c.primary {
				addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeError})
			}
			for len(rc.AttemptsSnapshot()) < models.MaxAttempts {
				addAttempt(rc, models.Attempt{Kind: models.AttemptModelFallback, Outcome: models.OutcomeSkipped,
					Reason: models.ReasonPolicy, Provider: "operator", Model: "own", CredentialType: models.CredentialBYOK})
			}

			p.ProcessResponse(context.Background(), rc)

			for i, a := range rc.AttemptsSnapshot() {
				if a.Outcome == models.OutcomeSkipped && (a.Billable || a.BilledCostMicros != 0 || a.UpstreamCostMicros != 0) {
					t.Fatalf("skipped attempt %d priced: %+v", i+1, a)
				}
			}
			// 1000 × $2/M + 500 × $10/M = 7,000 micros upstream, 7,350 billed.
			if b := rc.Billing; b.BilledMicros != 7350 || b.UpstreamMicros != 7000 {
				t.Fatalf("billing %+v; want the served answer billed 7350 on 7000 upstream", b)
			}
			if c.primary {
				if a := rc.AttemptsSnapshot()[0]; !a.Billable || a.BilledCostMicros != 7350 {
					t.Fatalf("primary %+v; want it priced as the attempt that served", a)
				}
			}
		})
	}
}
