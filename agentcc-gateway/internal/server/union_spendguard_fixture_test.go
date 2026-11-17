package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const unionSpendModel = "google/gemini-3-pro-image-preview"
const unionSpendBound int64 = 356_126_400

// An explicitly SYNTHETIC price fixture on a supported non-router image model.
// It is not a live-price claim or a router renamed to evade 746g. The independent
// exact oracle is (1,000,000*0.0003 + 65,536*0.0005 + 64*0.1)*1.05 USD.
// Keep the old 356/357 USD decision boundaries without re-enabling routers.
func unionSpendFixture(t *testing.T, g *managedTestGateway) {
	t.Helper()
	all, install := installBundledCatalogue(t)
	m := all[unionSpendModel]
	if m == nil || m.Inclusive == nil {
		t.Fatal("supported image fixture missing")
	}
	m.ContextLength, m.MaxOutputTokens = 1_000_000, 65536
	m.Prices = catalog.Prices{Prompt: 0.0003, Completion: 0.0005, Image: 0.1}
	m.Inclusive = &models.InclusivePrice{Contract: catalog.InclusiveFamilyContract, Model: unionSpendModel,
		ContextLimit: 1_000_000, OutputLimit: 65536, Prompt: "0.0003", Completion: "0.0005", Image: "0.1", TextOutput: true, ImageOutput: true}
	install(t, g)
	card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", unionSpendModel, "")
	if !ok || card.Inclusive == nil || card.Inclusive.RouterUniverse != "" {
		t.Fatal("fixture must be a supported non-router inclusive card")
	}
	bound, err := inclusive.Bound(catalogueSpendRequest(unionSpendModel, 64, 65536, 1), card.Inclusive)
	if err != nil || bound != unionSpendBound {
		t.Fatalf("independent exact bound=%d want=%d error=%v", bound, unionSpendBound, err)
	}
}
