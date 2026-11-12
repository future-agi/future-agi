package budget

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapPriceQualificationMatrix(t *testing.T) {
	for _, tc := range []struct {
		name    string
		card    models.PriceCard
		allowed bool
	}{
		{"explicit-free", models.PriceCard{Source: models.PriceSourceCatalog, ExplicitFree: true}, true},
		{"unknown-zero", models.PriceCard{Source: models.PriceSourceCatalog}, false},
		{"unknown-source", models.PriceCard{Request: 0.001}, false},
		{"media-dimension", models.PriceCard{Source: models.PriceSourceCatalog, Request: 0.001, Image: 0.01}, false},
		{"search-dimension", models.PriceCard{Source: models.PriceSourceCatalog, Request: 0.001, WebSearch: 0.01}, false},
		{"unbounded-reasoning", models.PriceCard{Source: models.PriceSourceCatalog, Request: 0.001, Reasoning: 0.01}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := capMoneyRequest(t, "org", "key-a", false)
			rc.Billing.EndpointSet = []models.EndpointRef{{Price: tc.card}}
			e := QualifyCapChat(rc, nil, 500, 256)
			if (e == nil) != tc.allowed {
				t.Fatalf("qualification=%v allowed=%v", e, tc.allowed)
			}
			if tc.allowed && RequestAdmission(rc).quoteMicros != 0 {
				t.Fatal("free card reserved positive spend")
			}
		})
	}
}

func TestCapLatePriceAndPassThroughMutationsRefused(t *testing.T) {
	rc := capMoneyRequest(t, "org", "key-a", true)
	rc.Billing.EndpointSet[0].Price.Request = 0.5
	if e := RecheckCapChat(rc); e == nil {
		t.Fatal("mutated exact price accepted")
	}
	rc = capMoneyRequest(t, "org", "key-a", false)
	rc.Request.Extra = map[string]json.RawMessage{"provider": json.RawMessage(`{"order":["unquoted"]}`)}
	if e := RecheckForwardedCapChat(rc); e == nil {
		t.Fatal("BYOK forwarded pass-through accepted")
	}
}
