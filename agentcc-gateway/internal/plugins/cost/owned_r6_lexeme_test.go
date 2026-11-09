package cost

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A valid reported cost whose JSON lexeme the exact decimal grammar rejects is
// priced exactly in G7 for the serving and the failed attempt alike.
func TestOwnedR6ReportedCostOutsideExactGrammar(t *testing.T) {
	for _, tc := range []struct {
		lexeme     string
		up, billed int64
	}{
		{"-0", 0, 0}, {"-0.0", 0, 0}, {"1e-100", 0, 0}, {"5e-0005", 50, 53}, {"0.5E-004", 50, 53},
		{"0.0000095" + strings.Repeat("0", 96), 10, 11},
	} {
		for _, serving := range []bool{false, true} {
			t.Run(fmt.Sprintf("%.12s/serving=%t", tc.lexeme, serving), func(t *testing.T) {
				var usage models.Usage
				if err := json.Unmarshal([]byte(`{"cost":`+tc.lexeme+`}`), &usage); err != nil || !usage.HasReportedCost() {
					t.Fatalf("fixture: %v", err)
				}
				up, billed, _, source, ok := PriceOwnedAttempt(&models.Attempt{CredentialType: models.CredentialManaged}, &usage, models.PriceCard{}, 500, 0)
				if !ok || up != tc.up || billed != tc.billed || source != string(models.CostSourceProviderReported) {
					t.Fatalf("PriceOwnedAttempt=(%d,%d,%s,%t) want=(%d,%d)", up, billed, source, ok, tc.up, tc.billed)
				}
				rc := managedRC(&usage)
				rc.Billing.OwnedRequest = true
				a := models.Attempt{AttemptID: "r6.1", Outcome: models.OutcomeSuccess, Usage: &usage}
				if !serving {
					rc.Response, a.Outcome = nil, models.OutcomeError
				}
				addAttempt(rc, a)
				billingPlugin(nil).FinalizeRequest(rc)
				got := rc.AttemptsSnapshot()[0]
				if !got.Billable || got.UpstreamCostMicros != tc.up || got.BilledCostMicros != tc.billed || rc.Billing.BilledMicros != tc.billed {
					t.Fatalf("G7=(%d,%d) request=%d billable=%t want=(%d,%d)", got.UpstreamCostMicros, got.BilledCostMicros, rc.Billing.BilledMicros, got.Billable, tc.up, tc.billed)
				}
			})
		}
	}
}

// Across merged stream events the earlier exact wire lexeme stays with its
// value, but a contested interim total is unresolved under the liability table.
// The attempt therefore consumes its full admission hold, not that partial total.
func TestOwnedR6MergedExactLexemeSurvivesLaterInvalidCost(t *testing.T) {
	for _, later := range []string{`null`, `"x"`, `-1`, `false`} {
		t.Run(later, func(t *testing.T) {
			var first, next models.Usage
			if json.Unmarshal([]byte(`{"prompt_tokens":1,"completion_tokens":1,"cost":0.00000949999999999999999}`), &first) != nil ||
				json.Unmarshal([]byte(`{"prompt_tokens":1,"completion_tokens":1,"cost":`+later+`}`), &next) != nil {
				t.Fatal("fixture")
			}
			merged := models.MergeUsage(&first, &next)
			rc := managedRC(merged)
			rc.Billing.OwnedRequest = true
			addAttempt(rc, models.Attempt{AttemptID: "r6.1", Outcome: models.OutcomeSuccess, Usage: merged, Pricing: &models.AttemptPricing{HoldMicros: 41580}})
			billingPlugin(nil).FinalizeRequest(rc)
			if b := rc.Billing; b.UpstreamMicros != 39600 || b.BilledMicros != 41580 {
				t.Fatalf("G7=(%d,%d) want=(39600,41580)", b.UpstreamMicros, b.BilledMicros)
			}
		})
	}
}
