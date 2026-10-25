package eligibility

import (
	"math"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func tags(set []*catalog.Endpoint) string {
	out := make([]string, len(set))
	for i, e := range set {
		out[i] = e.Tag
	}
	return strings.Join(out, ",")
}

// The endpoint set ranks by health × 1/price², keeps the top five, breaks
// ties in catalog order, and leaves out endpoints that are down or priced
// only by the upstream while a serving, priced one is eligible.
func TestEndpointSetRanking(t *testing.T) {
	uptime := func(u float64) func(*catalog.Endpoint) { return func(e *catalog.Endpoint) { e.UptimeLast30m = &u } }
	in := managedIn(nil, entry(
		ep("p1", 1e-6), ep("p2", 1e-6), // a tie: catalog order
		ep("cheap", 0.5e-6),
		ep("cheap-flaky", 0.5e-6, uptime(10)),
		ep("pricey", 4e-6),
		ep("mid", 2e-6),
		ep("down", 0.1e-6, func(e *catalog.Endpoint) { e.Status = -1 }),
		ep("dyn", 0, func(e *catalog.Endpoint) { e.DynamicPrice = true }),
	))
	v := Check(&in)
	if !v.OK {
		t.Fatalf("refused: %+v", v)
	}
	// cheap scores 4x p1; cheap-flaky (10% up at half the price) scores 0.4x p1.
	if got := tags(v.EndpointSet(MaxEndpointSet)); got != "cheap,p1,p2,cheap-flaky,mid" {
		t.Fatalf("set = %s", got)
	}
	if v.Endpoint == nil || v.Endpoint.Tag != "cheap" {
		t.Fatalf("best endpoint = %+v, want cheap", v.Endpoint)
	}
	if got := tags(v.EndpointSet(2)); got != "cheap,p1" {
		t.Fatalf("set of 2 = %s", got)
	}

	onlyDown := managedIn(nil, entry(ep("down", 1e-6, func(e *catalog.Endpoint) { e.Status = -2 })))
	if v := Check(&onlyDown); !v.OK || tags(v.EndpointSet(5)) != "down" {
		t.Fatalf("a model whose endpoints are all down keeps them: %+v", v)
	}

	byok := byokIn(nil)
	if v := Check(&byok); v.EndpointSet(5) != nil || v.Endpoint != nil {
		t.Fatalf("a BYOK candidate has no endpoint set: %+v", v)
	}
}

// A managed candidate is pinned to its own provider's endpoints only.
func TestEndpointsOfOtherProvidersIgnored(t *testing.T) {
	other := ep("direct", 0.1e-6, priceCard, func(e *catalog.Endpoint) { e.Provider = "mock-direct" })
	in := managedIn(nil, entry(other, ep("anthropic", 1e-6, zdr)))
	v := Check(&in)
	if !v.OK || tags(v.EndpointSet(5)) != "anthropic" {
		t.Fatalf("verdict %+v, set %s; want only openrouter's endpoint", v, tags(v.EndpointSet(5)))
	}
}

func TestPriceCardOfEndpoint(t *testing.T) {
	e := ep("anthropic", 3e-6, priceCard)
	card := PriceCard(&e, "snap1")
	if card.Prompt != 3e-6 || card.Completion != 6e-6 || card.Source != models.PriceSourcePriceCard || card.SnapshotID != "snap1" {
		t.Fatalf("card = %+v", card)
	}
	e2 := ep("deepinfra", 1e-6)
	if PriceCard(&e2, "").Source != models.PriceSourceCatalog {
		t.Fatal("an aggregator endpoint's card is the catalog's")
	}
}

func TestChooseSource(t *testing.T) {
	or := Source{ProviderID: "openrouter", Price: 3e-6, UpstreamFeeBps: 550, FeeKnown: true}
	direct := Source{ProviderID: "mock-direct", Price: 3e-6, UpstreamFeeBps: 0, FeeKnown: true}
	for _, tc := range []struct {
		name      string
		sources   []Source
		want      string
		wantRatio float64
	}{
		{"equal list price: the direct source's landed cost wins", []Source{or, direct}, "mock-direct", 1 / 1.055},
		{"order does not matter", []Source{direct, or}, "mock-direct", 1 / 1.055},
		{"within 1%: landed cost still decides", []Source{or, {ProviderID: "mock-direct", Price: 3.02e-6, FeeKnown: true}}, "mock-direct", 3.02 / (3 * 1.055)},
		{"over 1% more: the billed price decides", []Source{or, {ProviderID: "mock-direct", Price: 3.1e-6, FeeKnown: true}}, "openrouter", 3 * 1.055 / 3.1},
		{"an unknown fee is costed at the highest known fee, and loses the tie", []Source{{ProviderID: "no-endpoints", Price: 3e-6}, or}, "openrouter", 1},
		{"equal landed cost: health decides", []Source{
			{ProviderID: "a", Price: 1e-6, FeeKnown: true, HealthKnown: true, SuccessRate: 0.7},
			{ProviderID: "b", Price: 1e-6, FeeKnown: true, HealthKnown: true, SuccessRate: 0.99},
		}, "b", 1},
		{"one source", []Source{or}, "openrouter", 1},
		{"a direct source beats an aggregator with no endpoint data", []Source{{ProviderID: "openrouter", Price: 3e-6}, direct}, "mock-direct", 1 / 1.0},
		{"a source with no fixed price never wins at price 0", []Source{{ProviderID: "dynamic", FeeKnown: true}, or}, "openrouter", 1},
		{"with no fixed price anywhere, the lower fee decides", []Source{{ProviderID: "a", UpstreamFeeBps: 550, FeeKnown: true}, {ProviderID: "b", FeeKnown: true}}, "b", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			best, ratio := ChooseSource(tc.sources)
			if tc.sources[best].ProviderID != tc.want || math.Abs(ratio-tc.wantRatio) > 1e-9 {
				t.Fatalf("chose %s with landed ratio %v, want %s and %v", tc.sources[best].ProviderID, ratio, tc.want, tc.wantRatio)
			}
		})
	}
	if best, _ := ChooseSource(nil); best != -1 {
		t.Fatal("no sources must choose none")
	}
}
