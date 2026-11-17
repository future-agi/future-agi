package catalog

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

// Decimal preservation is still tested without treating a catalogue-wide
// mathematical envelope as permission to sell a router (746g).
func TestR602NumericEndpointHold(t *testing.T) {
	endpoints, err := ParseOpenRouterEndpoints("synthetic/target", []byte(`{"data":{"id":"synthetic/target","endpoints":[{"provider_name":"Synthetic","context_length":1,"pricing":{"prompt":0,"completion":0.00000949999999999999999}}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	e := endpoints[0]
	cards := sourceRouterCards("synthetic/target", e.admissionPricing, e.Prices, e.PriceTiers)
	plan, err := routerbilling.Bound("openrouter/auto", "synthetic-math-only", false, cards, routerbilling.Caps{InputTokens: 1, MaxTokens: 1, N: 1}, 1000)
	if err != nil || plan.HoldMicros() != 9 {
		t.Fatalf("endpoint decimal rounded through float: hold=%d want=9 err=%v", plan.HoldMicros(), err)
	}
}

func TestR602ExactRouterRefreshVersions(t *testing.T) {
	s := NewStore()
	put := func(rate string) {
		endpoints, err := ParseOpenRouterEndpoints("synthetic/target", []byte(fmt.Sprintf(`{"data":{"id":"synthetic/target","endpoints":[{"provider_name":"Synthetic","context_length":1,"pricing":{"prompt":0,"completion":%s}}]}}`, rate)))
		if err != nil {
			t.Fatal(err)
		}
		s.Put(&Snapshot{ProviderID: "openrouter", CatalogSource: SourceOpenRouter, Models: map[string]*Model{
			"openrouter/auto":  {ID: "openrouter/auto", DynamicPrice: true, InclusiveEnabled: true, ContextLength: 1},
			"synthetic/target": {ID: "synthetic/target", ContextLength: 1, Endpoints: endpoints},
		}})
	}
	put("0.00000949999999999999999")
	old := s.Load()
	put("0.0000095")
	for i, cat := range []*Catalog{old, s.Load()} {
		if _, ok := cat.PriceCard("openrouter", "openrouter/auto", ""); ok || cat.ServesManaged("openrouter", "openrouter/auto") {
			t.Fatal("refresh re-enabled router")
		}
		m, _ := cat.Get("openrouter", "synthetic/target")
		e := m.Endpoints[0]
		cards := sourceRouterCards(m.ID, e.admissionPricing, e.Prices, e.PriceTiers)
		plan, err := routerbilling.Bound("openrouter/auto", "synthetic-math-only", false, cards, routerbilling.Caps{InputTokens: 1, MaxTokens: 1, N: 1}, 1000)
		want := []int64{9, 11}[i]
		if err != nil || plan.HoldMicros() != want {
			t.Fatalf("frozen endpoint decimals: hold=%d want=%d err=%v", plan.HoldMicros(), want, err)
		}
	}
	before := s.Load().ID()
	put("0.000010")
	if s.Load().ID() == before {
		t.Fatal("changed fixed endpoint price reused old version")
	}
}
