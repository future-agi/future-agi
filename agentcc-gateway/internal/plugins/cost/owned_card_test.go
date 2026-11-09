package cost

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOwnedR5TokenCardAndNativeTotalsUseExactRounding(t *testing.T) {
	for _, tc := range []struct {
		name       string
		rate       float64
		up, billed int64
	}{
		{"below-half", 0.0000094999999, 9, 9},
		{"half", 0.0000095, 10, 11},
		{"small-half", 0.0000025, 3, 3},
	} {
		for _, reported := range []bool{false, true} {
			t.Run(tc.name+map[bool]string{false: "/card", true: "/native"}[reported], func(t *testing.T) {
				p := billingPlugin(fixedLookup(models.PriceCard{Prompt: tc.rate, Source: models.PriceSourceCatalog}))
				u := &models.Usage{PromptTokens: 1}
				if reported {
					u.Cost = usd(tc.rate) // native usage has no JSON CostDecimal
				}
				rc := managedRC(u)
				rc.Billing.OwnedRequest = true
				addAttempt(rc, models.Attempt{AttemptID: "exact.1", Outcome: models.OutcomeSuccess, Usage: u})
				p.FinalizeRequest(rc)
				if b := rc.Billing; b.UpstreamMicros != tc.up || b.BilledMicros != tc.billed {
					t.Fatalf("want=(%d,%d) got=(%d,%d)", tc.up, tc.billed, b.UpstreamMicros, b.BilledMicros)
				}
			})
		}
	}
}
