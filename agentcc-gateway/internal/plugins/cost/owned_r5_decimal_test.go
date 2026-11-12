package cost

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOwnedR5FinalizeExactDecimalBoundaries(t *testing.T) {
	p := billingPlugin(nil)
	for _, serving := range []bool{false, true} {
		t.Run(fmt.Sprintf("serving=%t", serving), func(t *testing.T) {
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
				rc := managedRC(&usage)
				rc.Billing.OwnedRequest = true
				a := models.Attempt{AttemptID: "exact.1", Outcome: models.OutcomeSuccess, Usage: &usage}
				if !serving {
					rc.Response, a.Outcome = nil, models.OutcomeError
				}
				addAttempt(rc, a)
				p.FinalizeRequest(rc)
				got := rc.AttemptsSnapshot()[0]
				if got.UpstreamCostMicros != up || got.BilledCostMicros != billed || rc.Billing.BilledMicros != billed {
					t.Fatalf("raw=%s want=(%d,%d) got=(%d,%d) request=%d", raw, up, billed, got.UpstreamCostMicros, got.BilledCostMicros, rc.Billing.BilledMicros)
				}
			}
		})
	}
}
