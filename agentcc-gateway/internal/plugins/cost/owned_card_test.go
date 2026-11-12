package cost

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOwnedR5TokenCardAndNativeTotalsUseExactRounding(t *testing.T) {
	// The hold admission reserved for the /card attempts.
	const hold = 41580
	for _, tc := range []struct {
		name       string
		rate       float64
		up, billed int64
	}{
		{"below-half", 0.0000094999999, 9, 9},
		{"half", 0.0000095, 10, 11},
		{"small-half", 0.0000025, 3, 3},
	} {
		for _, variant := range []string{"card", "card-both-counts", "native"} {
			t.Run(tc.name+"/"+variant, func(t *testing.T) {
				p := billingPlugin(fixedLookup(models.PriceCard{Prompt: tc.rate, Source: models.PriceSourceCatalog}))
				u := &models.Usage{PromptTokens: 1}
				a := models.Attempt{AttemptID: "exact.1", Outcome: models.OutcomeSuccess}
				up, billed, estimated := tc.up, tc.billed, false
				switch variant {
				case "card":
					// A prompt count alone does not price the attempt: since
					// 99d6a370, OwnedLiability prices from tokens only when both
					// counts are reported, so it settles at the full hold,
					// 41580 billed and 41580*100/105 = 39600 upstream.
					// Partial usage on an owned attempt is charged the full hold; to change with the owner's amount decision.
					a.Pricing = models.NewAttemptPricing(&models.BillingState{AttemptHoldMicros: hold})
					up, billed, estimated = hold*100/105, hold, true
				case "card-both-counts":
					// Both counts reported: the token card prices it, with exact
					// rounding, and the hold is not charged.
					u = models.MeasuredUsage(1, 1)
					a.Pricing = models.NewAttemptPricing(&models.BillingState{AttemptHoldMicros: hold})
				case "native":
					u.Cost = usd(tc.rate) // native usage has no JSON CostDecimal
				}
				a.Usage = u
				rc := managedRC(u)
				rc.Billing.OwnedRequest = true
				addAttempt(rc, a)
				p.FinalizeRequest(rc)
				if b := rc.Billing; b.UpstreamMicros != up || b.BilledMicros != billed {
					t.Fatalf("want=(%d,%d) got=(%d,%d)", up, billed, b.UpstreamMicros, b.BilledMicros)
				}
				if variant != "native" && rc.Billing.UsageEstimated != estimated {
					t.Fatalf("usage estimated = %v, want %v", rc.Billing.UsageEstimated, estimated)
				}
			})
		}
	}
}
