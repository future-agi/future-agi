package server

import (
	"context"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestAttemptPricingIsASnapshot: an attempt keeps the cards it was admitted
// on. A change the request's billing state takes afterwards, even one made in
// place on its endpoint set or reserve card, never reaches the attempt that
// already started.
func TestAttemptPricingIsASnapshot(t *testing.T) {
	acme := models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog, SnapshotID: "s1"}
	bolt := models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog, SnapshotID: "s1"}
	admitted := []models.EndpointRef{
		{ID: "openrouter:acme:m", UpstreamProvider: "acme", Price: acme},
		{ID: "openrouter:bolt:m", UpstreamProvider: "bolt", Price: bolt},
	}

	h := &Handlers{}
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = "req-pricing-snapshot"
	rc.Request = &models.ChatCompletionRequest{Model: "acme/m"}
	rc.Billing.EndpointSet = append([]models.EndpointRef(nil), admitted...)
	reserve := acme
	rc.Billing.ReservePrice = &reserve

	_, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", "acme/m", models.CredentialManaged)
	if refusal != "" {
		t.Fatalf("attempt refused: %q", refusal)
	}
	defer finish(nil, nil, nil)

	rc.Billing.EndpointSet[0].Price.Prompt = 9e-6
	rc.Billing.EndpointSet[1].UpstreamProvider = "zeta"
	rc.Billing.ReservePrice.Completion = 90e-6

	got := rc.AttemptsSnapshot()
	if len(got) != 1 || got[0].Pricing == nil {
		t.Fatalf("attempts %+v, want one with admitted prices", got)
	}
	p := got[0].Pricing
	if !reflect.DeepEqual(p.EndpointSet, admitted) {
		t.Errorf("attempt endpoint set %+v, want the admitted %+v", p.EndpointSet, admitted)
	}
	if p.ReservePrice == nil || *p.ReservePrice != acme {
		t.Errorf("attempt reserve card %+v, want the admitted %+v", p.ReservePrice, acme)
	}
}
