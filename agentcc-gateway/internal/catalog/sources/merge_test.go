package sources

import (
	"sort"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// orClaude35 is an OpenRouter /models list with anthropic/claude-3.5-sonnet
// and the endpoints list of its one upstream.
const (
	orClaude35 = `{"data":[{"id":"anthropic/claude-3.5-sonnet","canonical_slug":"anthropic/claude-3.5-sonnet",
		"name":"Anthropic: Claude 3.5 Sonnet","context_length":200000,
		"pricing":{"prompt":"0.000003","completion":"0.000015"}}]}`
	orClaude35Endpoints = `{"data":{"id":"anthropic/claude-3.5-sonnet","endpoints":[{"name":"Anthropic | anthropic/claude-3.5-sonnet",
		"provider_name":"Anthropic","tag":"anthropic","context_length":200000,
		"pricing":{"prompt":"0.000003","completion":"0.000015"},"status":0}]}}`
)

// TestCanonicalMergeAcrossSources: an OpenRouter source and a direct
// price-card source for anthropic/claude-3.5-sonnet are one canonical model
// with two endpoints, each with its source's fee: 550 through OpenRouter
// (its default), 0 direct.
func TestCanonicalMergeAcrossSources(t *testing.T) {
	orModels, err := catalog.ParseOpenRouter([]byte(orClaude35))
	if err != nil {
		t.Fatal(err)
	}
	eps, err := catalog.ParseOpenRouterEndpoints("anthropic/claude-3.5-sonnet", []byte(orClaude35Endpoints))
	if err != nil {
		t.Fatal(err)
	}
	orModels["anthropic/claude-3.5-sonnet"].Endpoints = eps
	card, err := ParsePriceCard(fixture(t, "pricecard_direct.json"))
	if err != nil {
		t.Fatal(err)
	}

	store := catalog.NewStore()
	if err := store.Configure(catalog.Options{UpstreamFeeBps: map[string]int{"anthropic-direct": 0}}); err != nil {
		t.Fatal(err)
	}
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", CatalogSource: catalog.SourceOpenRouter, Models: orModels})
	store.Put(card.Snapshot("anthropic-direct", cardNow))

	cm, ok := store.Load().CanonicalModel("anthropic/claude-3.5-sonnet")
	if !ok {
		t.Fatal("no canonical anthropic/claude-3.5-sonnet")
	}
	if len(cm.Listings) != 2 || len(cm.Endpoints) != 2 {
		t.Fatalf("merged = %d listings, endpoints %+v", len(cm.Listings), cm.Endpoints)
	}
	type got struct {
		provider, source, upstream string
		fee                        int
	}
	var all []got
	for _, e := range cm.Endpoints {
		all = append(all, got{e.Provider, e.Source, e.UpstreamProvider, e.UpstreamFeeBps})
	}
	sort.Slice(all, func(i, j int) bool { return all[i].provider < all[j].provider })
	want := []got{
		{"anthropic-direct", catalog.SourcePriceCard, "anthropic", 0},
		{"openrouter", catalog.SourceOpenRouter, "anthropic", 550},
	}
	if len(all) != 2 || all[0] != want[0] || all[1] != want[1] {
		t.Fatalf("endpoints = %+v, want %+v", all, want)
	}
	// Each listing keeps its own model id, the one sent upstream.
	ids := map[string]string{}
	for _, l := range cm.Listings {
		ids[l.ProviderID] = l.Model.ID
	}
	if ids["openrouter"] != "anthropic/claude-3.5-sonnet" || ids["anthropic-direct"] != "claude-3-5-sonnet-20241022" {
		t.Errorf("listings = %v", ids)
	}
}
