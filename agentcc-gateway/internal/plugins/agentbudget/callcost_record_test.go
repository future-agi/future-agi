package agentbudget

// MIXED-HOP-GUARD-1 round 3: a run budget counts what its call was billed,
// as the cost plugin billed it, whatever the attempt record held. This file
// uses only APIs that exist at 4e38b7961b (the round-2 head), where callCost
// missed the serving attempt past models.MaxAttempts and counted nothing for
// a served request whose recorded attempts were all skipped.

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// recordCall is a managed call that sent failed attempts, charged ones
// billed 105 micros as the cost plugin bills them, then the serving attempt,
// billed 18,375.
func recordCall(t *testing.T, failed int, charged func(int) bool) *models.RequestContext {
	t.Helper()
	rc := call(t, 0)
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	for i := range failed {
		a := models.Attempt{Kind: models.AttemptModelFallback, Provider: "p", Model: fmt.Sprintf("failed-%d", i),
			CredentialType: models.CredentialManaged, Outcome: models.OutcomeError}
		if charged(i) {
			cost := 0.0001
			a.PromptTokens, a.Usage = 10, &models.Usage{PromptTokens: 10, Cost: &cost}
			a.Billable, a.UpstreamCostMicros, a.BilledCostMicros = true, 100, 105
		}
		rc.AddAttempt(a)
	}
	served := rc.AddAttempt(models.Attempt{Kind: models.AttemptModelFallback, Provider: "p", Model: "serving",
		CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess})
	rc.UpdateAttempt(served.AttemptID, func(a *models.Attempt) {
		a.Billable, a.UpstreamCostMicros, a.BilledCostMicros = true, 17500, 18375
	})
	return rc
}

func TestCallCostCountsTheServingAttemptPastTheRecord(t *testing.T) {
	none := func(int) bool { return false }
	odd := func(i int) bool { return i%2 == 1 }
	for _, c := range []struct {
		name    string
		failed  int
		charged func(int) bool
		want    int64
	}{
		{"15 chargeless failures (control)", models.MaxAttempts - 1, none, 18375},
		{"16 chargeless failures", models.MaxAttempts, none, 18375},
		{"40 chargeless failures", 40, none, 18375},
		{"20 failures, the odd ones charged", 20, odd, 18375 + 10*105},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := callCost(recordCall(t, c.failed, c.charged)); got != c.want {
				t.Fatalf("callCost = %d; want %d, the serving attempt and every charged failure", got, c.want)
			}
		})
	}
}

// A served call whose recorded attempts were all skipped was priced by the
// cost plugin as the one attempt it made (rc.Billing): its run counts that,
// as it would with no attempt recorded at all.
func TestCallCostOfASkippedOnlyRecordIsTheRequestsBilling(t *testing.T) {
	for _, mode := range []string{models.BillingModeManaged, models.BillingModeBYOK} {
		t.Run(mode, func(t *testing.T) {
			rc := call(t, 0)
			rc.Metadata["billing_mode"] = mode
			for range 3 {
				rc.AddAttempt(models.Attempt{Kind: models.AttemptModelFallback, Provider: "p", Model: "skipped",
					Outcome: models.OutcomeSkipped, Reason: models.ReasonPolicy})
			}
			rc.Billing.BilledMicros, rc.Billing.UpstreamMicros = 7350, 7000
			want := int64(7000)
			if mode == models.BillingModeManaged {
				want = 7350
			}
			if got := callCost(rc); got != want {
				t.Fatalf("callCost = %d; want %d, the request's own %s billing", got, want, mode)
			}
		})
	}
}
