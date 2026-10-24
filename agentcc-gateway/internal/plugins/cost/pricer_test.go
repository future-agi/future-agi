package cost

import (
	"context"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestPricedByEndpoint(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	pr := NewPricer(func() *modeldb.ModelDB { return db }, nil)
	cases := []struct {
		endpoint, model string
		want            bool
	}{
		{"chat.completions", "gpt-4o", true},
		{"chat", "gpt-4o", true},
		{"embeddings", "text-embedding-3-small", true},
		{"images", "dall-e-3", true},
		{"audio.speech", "tts-1", true},
		{"audio.transcriptions", "whisper-1", true},
		{"rerank", "rerank-english-v3.0", true},
		// A model is priced only for what it charges for.
		{"images", "gpt-4o", false},
		{"audio.speech", "gpt-4o", false},
		{"chat.completions", "tts-1", false},
		{"chat.completions", "nobody/knows", false},
		{"assistants", "gpt-4o", false},
	}
	for _, c := range cases {
		if got := pr.Priced(c.endpoint, c.model); got != c.want {
			t.Errorf("Priced(%q, %q) = %v, want %v", c.endpoint, c.model, got, c.want)
		}
	}
}

// The injected lookup is read on every call, never at construction: a
// lookup the wiring replaced after the pricer was built is the one it uses
// (the late-bound holder).
func TestPricerReadsTheInjectedLookupAtCallTime(t *testing.T) {
	db := modeldb.New(nil, nil)
	var current atomic.Pointer[models.PriceLookup]
	empty := models.PriceLookup(func(_, _, _ string) (models.PriceCard, bool) { return models.PriceCard{}, false })
	current.Store(&empty)
	pr := NewPricer(func() *modeldb.ModelDB { return db }, func() models.PriceLookup { return *current.Load() })

	if pr.Priced("chat.completions", "openrouter/vendor/catalog-only") {
		t.Fatal("nothing prices the model yet")
	}
	catalog := models.PriceLookup(func(providerID, model, _ string) (models.PriceCard, bool) {
		if providerID == "openrouter" && model == "vendor/catalog-only" {
			return models.PriceCard{Prompt: 1e-6, Completion: 2e-6, Source: models.PriceSourceCatalog}, true
		}
		return models.PriceCard{}, false
	})
	current.Store(&catalog)
	if !pr.Priced("chat.completions", "openrouter/vendor/catalog-only") {
		t.Fatal("the replaced lookup was not read on the next call")
	}

	// The plugin prices a catalog-only model from it too, as catalog.
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetManagedMarkup(5)
	p.SetPricer(pr)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Model = "vendor/catalog-only"
	p.ProcessResponse(context.Background(), rc)
	if rc.Billing.UpstreamMicros != 2000 || rc.Metadata["cost_source"] != "catalog" {
		t.Fatalf("upstream %d, source %q; want 2000 from the catalog", rc.Billing.UpstreamMicros, rc.Metadata["cost_source"])
	}
}

// A model database card the injected default returns carries no cache-write
// or request price; the pricer reads the model database's own, fuller card.
func TestModelDBCardFromTheInjectedDefaultIsEnriched(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	thin := models.PriceLookup(func(_, _, _ string) (models.PriceCard, bool) {
		return models.PriceCard{Prompt: 3e-6, Completion: 15e-6, Source: models.PriceSourceModelDB}, true
	})
	pr := NewPricer(func() *modeldb.ModelDB { return db }, func() models.PriceLookup { return thin })
	card, source, ok := pr.Card("anthropic", "claude-sonnet-4-5", "", false)
	if !ok || card.CacheWrite != 3.75e-6 || source != models.CostSourceModelDB {
		t.Fatalf("card = %+v (%s); want the model database's cache-write price", card, source)
	}
}
