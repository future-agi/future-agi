package catalog

import "testing"

// TestStatsCountCanonicalModelsPerSource: a model two sources list counts
// once overall and once in each source; variants count once.
func TestStatsCountCanonicalModelsPerSource(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: map[string]*Model{
		"anthropic/claude-haiku-4.5":       {CanonicalSlug: "anthropic/claude-4.5-haiku-20251001", Prices: Prices{Prompt: 1e-6}},
		"anthropic/claude-haiku-4.5:batch": {CanonicalSlug: "anthropic/claude-4.5-haiku-20251001", Prices: Prices{Prompt: 5e-7}},
		"openai/gpt-4o":                    {Prices: Prices{Prompt: 2.5e-6}},
	}})
	s.Put(&Snapshot{ProviderID: "card", CatalogSource: SourcePriceCard, Models: map[string]*Model{
		"claude-haiku-4-5-20251001": {Prices: Prices{Prompt: 1e-6}},
	}})
	st := s.Stats()
	if st.CanonicalModels != 2 {
		t.Errorf("canonical_models = %d, want 2 (haiku through two sources, gpt-4o)", st.CanonicalModels)
	}
	if got := st.PerSource[SourceOpenRouter].CanonicalModels; got != 2 {
		t.Errorf("openrouter canonical_models = %d, want 2", got)
	}
	if got := st.PerSource[SourcePriceCard].CanonicalModels; got != 1 {
		t.Errorf("price_card canonical_models = %d, want 1", got)
	}
}
