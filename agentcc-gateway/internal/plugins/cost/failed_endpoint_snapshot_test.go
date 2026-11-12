package cost

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestManagedAccountingFailedEndpointSnapshot(t *testing.T) {
	cheap := models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourceCatalog}
	dear := models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog}
	for _, tc := range []struct {
		name, served string
		ambiguous    bool
		want         int64
		source       models.CostSource
	}{
		{"known", "Cheap Compute", false, 48, models.CostSourceCatalog},
		{"unknown", "Not admitted", false, 97, models.CostSourceCatalogMax},
		{"missing", "", false, 97, models.CostSourceCatalogMax},
		{"ambiguous", "Cheap Compute", true, 97, models.CostSourceCatalogMax},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := billingPlugin(fixedLookup(cheap))
			rc := managedRC(nil)
			rc.Response = nil
			second := "Expensive Compute"
			if tc.ambiguous {
				second = "Cheap Compute"
			}
			pricing := &models.AttemptPricing{ReservePrice: &dear, EndpointSet: []models.EndpointRef{{UpstreamProvider: "cheap", ProviderName: "Cheap Compute", Price: cheap}, {UpstreamProvider: "expensive", ProviderName: second, Price: dear}}}
			addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeError, Pricing: pricing, ServedUpstreamProvider: tc.served, Usage: &models.Usage{PromptTokens: 8, CompletionTokens: 3}})
			// A later route's cheap identity/card cannot be borrowed by this failure.
			rc.Billing.ServedUpstreamProvider = "Cheap Compute"
			rc.Billing.ReservePrice = &cheap
			rc.Billing.EndpointSet = []models.EndpointRef{{ProviderName: "Not admitted", Price: cheap}}
			p.FinalizeRequest(rc)
			a := rc.AttemptsSnapshot()[0]
			if a.BilledCostMicros != tc.want || a.CostSource != tc.source {
				t.Fatalf("billed=%d want=%d source=%s want=%s", a.BilledCostMicros, tc.want, a.CostSource, tc.source)
			}
		})
	}
}
