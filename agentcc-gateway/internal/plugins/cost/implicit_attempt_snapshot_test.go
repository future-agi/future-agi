package cost

import (
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestImplicitAttemptPricingIsASnapshot: the one attempt a request with no
// recorded attempt is priced as keeps the cards it was made with, however the
// request's endpoint set or reserve card is changed in place afterwards.
func TestImplicitAttemptPricingIsASnapshot(t *testing.T) {
	acme := models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog, SnapshotID: "s1"}
	bolt := models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog, SnapshotID: "s1"}
	admitted := []models.EndpointRef{
		{ID: "openrouter:acme:m", UpstreamProvider: "acme", Price: acme},
		{ID: "openrouter:bolt:m", UpstreamProvider: "bolt", Price: bolt},
	}
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.EndpointSet = append([]models.EndpointRef(nil), admitted...)
	reserve := acme
	rc.Billing.ReservePrice = &reserve

	a := implicitAttempt(rc)
	rc.Billing.EndpointSet[0].Price.Prompt = 9e-6
	rc.Billing.EndpointSet[1].UpstreamProvider = "zeta"
	rc.Billing.ReservePrice.Completion = 90e-6

	if a.Pricing == nil {
		t.Fatal("implicit attempt has no admitted prices")
	}
	if !reflect.DeepEqual(a.Pricing.EndpointSet, admitted) {
		t.Errorf("attempt endpoint set %+v, want the admitted %+v", a.Pricing.EndpointSet, admitted)
	}
	if a.Pricing.ReservePrice == nil || *a.Pricing.ReservePrice != acme {
		t.Errorf("attempt reserve card %+v, want the admitted %+v", a.Pricing.ReservePrice, acme)
	}
}
