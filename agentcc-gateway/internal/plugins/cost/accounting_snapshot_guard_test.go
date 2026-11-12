package cost

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestManagedAccountingEmptySnapshotNeverBorrowsLaterReserve(t *testing.T) {
	live := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourceCatalog}
	later := models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog}
	p := billingPlugin(fixedLookup(live))
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.ReservePrice = &later
	a := models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess, Pricing: &models.AttemptPricing{}}
	addAttempt(rc, a)
	p.FinalizeRequest(rc)
	if got := rc.AttemptsSnapshot()[0].UpstreamCostMicros; got != 7000 {
		t.Fatalf("empty snapshot borrowed later reserve: %d want own catalogue 7000", got)
	}
}

func TestManagedAccountingUnusedPricedHoldIsNotUnpriced(t *testing.T) {
	card := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourceCatalog}
	p := billingPlugin(fixedLookup(card))
	rc := managedRC(nil)
	rc.Response = nil
	rc.Billing.ReservePrice = &card
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeCancelled, Pricing: models.NewAttemptPricing(&rc.Billing)})
	p.FinalizeRequest(rc)
	if rc.Billing.BilledMicros != 0 || rc.Billing.UsageEstimated || rc.Billing.CostSource != models.CostSourceCatalog {
		t.Fatalf("unused admitted hold has misleading provenance: %+v", rc.Billing)
	}
}
