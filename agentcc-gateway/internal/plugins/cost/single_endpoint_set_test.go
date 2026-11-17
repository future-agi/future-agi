package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestSingleEndpointSetUnknownServedUsesItsCard: a direct
// price-card source names no served upstream, and its managed route's
// endpoint set is the card's one endpoint, so the attempt is priced on that
// card and recorded as price_card, not catalog_max. A set of two with no
// served endpoint, or a served endpoint outside a set of one, still takes the
// reserve card (TestUnknownServedUsesReservePrice).
func TestSingleEndpointSetUnknownServedUsesItsCard(t *testing.T) {
	p := billingPlugin(nil)
	card := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourcePriceCard}
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.EndpointSet = []models.EndpointRef{{UpstreamProvider: "mock-direct", Price: card}}
	rc.Billing.ReservePrice = &card
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	a := rc.AttemptsSnapshot()[0]
	if a.UpstreamCostMicros != 7000 || a.CostSource != models.CostSourcePriceCard {
		t.Fatalf("attempt = %+v; want the card's 7000 micros, price_card", a)
	}

	// A served endpoint outside the set of one keeps the reserve card.
	rc = managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.EndpointSet = []models.EndpointRef{{UpstreamProvider: "mock-direct", Price: card}}
	rc.Billing.ReservePrice = &card
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess, ServedUpstreamProvider: "zeta"})
	p.ProcessResponse(context.Background(), rc)
	if a := rc.AttemptsSnapshot()[0]; a.CostSource != models.CostSourceCatalogMax {
		t.Fatalf("served outside the set: cost source %q, want catalog_max", a.CostSource)
	}
}
