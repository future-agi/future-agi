package catalog

import (
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ModelInfos converts the catalog to model database entries keyed
// "<provider>/<model>", so the cost plugin prices a managed request at the
// managed provider's price rather than a direct provider's. Models with no
// fixed price are left out; only the upstream's reported cost prices them.
func (c *Catalog) ModelInfos() map[string]*modeldb.ModelInfo {
	out := make(map[string]*modeldb.ModelInfo, c.Len())
	for _, snap := range c.Snapshots() {
		for _, m := range snap.Models {
			if !m.Priced() {
				continue
			}
			key := snap.ProviderID + "/" + m.ID
			out[key] = m.ModelInfo(key, snap.ProviderID)
		}
	}
	return out
}

// ModelInfo describes m as a model database entry. The model database holds
// the prompt, completion, cache-read and input-image prices; the full card,
// cache writes, requests, web search and reasoning included, is PriceCard's.
func (m *Model) ModelInfo(id, providerID string) *modeldb.ModelInfo {
	mode := modeldb.ModeChat
	if !m.Produces("text") && m.Produces("image") {
		mode = modeldb.ModeImageGeneration
	}
	maxIn := m.ContextLength
	if maxIn > 0 && m.MaxOutputTokens > 0 && m.MaxOutputTokens < maxIn {
		maxIn -= m.MaxOutputTokens
	}
	return &modeldb.ModelInfo{
		ID:              id,
		Provider:        providerID,
		Mode:            mode,
		MaxInputTokens:  maxIn,
		MaxOutputTokens: m.MaxOutputTokens,
		Pricing: modeldb.PricingInfo{
			InputPerToken:       m.Prices.Prompt,
			OutputPerToken:      m.Prices.Completion,
			CachedInputPerToken: m.Prices.CacheRead,
			InputPerImage:       m.Prices.Image,
		},
		Capabilities: modeldb.CapabilityFlags{
			FunctionCalling:   m.Supports("tools"),
			ParallelToolCalls: m.Supports("parallel_tool_calls") || m.Supports("tools"),
			Vision:            m.Accepts("image"),
			AudioInput:        m.Accepts("audio"),
			AudioOutput:       m.Produces("audio"),
			PDFInput:          m.Accepts("file"),
			Streaming:         true,
			ResponseSchema:    m.Supports("structured_outputs"),
			SystemMessages:    true,
			PromptCaching:     m.Prices.CacheRead > 0 || m.Prices.CacheWrite > 0,
			Reasoning:         m.Supports("reasoning") || m.Supports("include_reasoning"),
		},
	}
}

// PriceCard returns what model costs through providerID: endpointID's card
// when the model lists that endpoint, else the model's own card. A model the
// provider does not list, or whose price is dynamic, unknown or stale, has
// no card. The card names the catalog version it was read from, and its
// source: price_card for a price-card source, else catalog. A card for a
// long prompt may be in PriceTiers; this is the base card.
func (s *Store) PriceCard(providerID, model, endpointID string) (models.PriceCard, bool) {
	cat := s.Load()
	m, ok := cat.Get(providerID, model)
	if !ok || !m.Priced() {
		return models.PriceCard{}, false
	}
	source := models.PriceSourceCatalog
	if cat.byProvider[providerID].CatalogSource == SourcePriceCard {
		source = models.PriceSourcePriceCard
	}
	p := m.Prices
	if endpointID != "" {
		if e, ok := m.Endpoint(endpointID); ok {
			if e.DynamicPrice {
				return models.PriceCard{}, false
			}
			p = e.Prices
		}
	}
	return models.PriceCard{
		Prompt: p.Prompt, Completion: p.Completion, CacheRead: p.CacheRead, CacheWrite: p.CacheWrite,
		Request: p.Request, Image: p.Image, WebSearch: p.WebSearch, Reasoning: p.Reasoning,
		Source: source, SnapshotID: cat.ID(),
	}, true
}

// PriceLookup is the catalog-first price lookup the gateway injects: the
// catalog's card when it has one, else fallback's.
func (s *Store) PriceLookup(fallback models.PriceLookup) models.PriceLookup {
	return func(providerID, model, endpointID string) (models.PriceCard, bool) {
		if card, ok := s.PriceCard(providerID, model, endpointID); ok {
			return card, true
		}
		if fallback == nil {
			return models.PriceCard{}, false
		}
		return fallback(providerID, model, endpointID)
	}
}
