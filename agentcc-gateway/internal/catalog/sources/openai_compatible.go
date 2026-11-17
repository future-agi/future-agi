// Package sources reads the managed catalog sources other than OpenRouter's:
// one generic parser for an OpenAI-compatible GET /models list
// (catalog_source: openai_compatible) and static price cards
// (catalog_source: price_card). Resolve attaches the right reader to each
// managed provider's catalog.Source at startup.
package sources

import (
	"encoding/json"
	"errors"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// listedModel is the part of an OpenAI-compatible /models entry the parser
// reads: the id, and, when present in OpenRouter's shape, the pricing and
// context length. Every other field is ignored, so providers that differ
// only in base URL (Together, Fireworks, DeepInfra, Groq, the Hugging Face
// router) all parse here.
type listedModel struct {
	ID            string         `json:"id"`
	ContextLength int            `json:"context_length"`
	Pricing       map[string]any `json:"pricing"`
}

// ParseOpenAICompatible reads a GET {base_url}/models response: either
// {"data": [...]} or a bare array of model objects (Together's). A model
// whose pricing is in OpenRouter's shape (USD per token) and can bill a call
// by itself (catalog.Prices.BillsCall) is priced and gets one endpoint on
// upstreamSlug. Any other model is listed unpriced, with no endpoint, and is
// never offered as managed: one with no price, a dynamic ("-1") price, or
// only lines that cannot price a chat call (all zero, image only). Unlike
// OpenRouter, such an upstream reports no cost, so its call would finalize
// unpriced.
func ParseOpenAICompatible(body []byte, upstreamSlug string) (map[string]*catalog.Model, error) {
	entries, err := decodeList(body)
	if err != nil {
		return nil, err
	}
	out := make(map[string]*catalog.Model, len(entries))
	for _, e := range entries {
		if e.ID == "" {
			continue
		}
		m := &catalog.Model{ID: e.ID, ContextLength: e.ContextLength}
		prices, tiers, dynamic, listed := catalog.ParsePricing(e.Pricing)
		if !listed || dynamic || !prices.BillsCall() {
			m.Unpriced = true
			out[e.ID] = m
			continue
		}
		m.Prices, m.PriceTiers = prices, tiers
		m.Endpoints = []catalog.Endpoint{{
			ID:               catalog.EndpointID(catalog.SourceOpenAICompatible, upstreamSlug, e.ID),
			Source:           catalog.SourceOpenAICompatible,
			UpstreamProvider: upstreamSlug,
			Tag:              upstreamSlug,
			ContextLength:    e.ContextLength,
			Prices:           prices,
			PriceTiers:       tiers,
			DataCollection:   catalog.DataCollectionUnknown,
		}}
		out[e.ID] = m
	}
	if len(out) == 0 {
		return nil, errors.New("openai-compatible model list: no models")
	}
	return out, nil
}

// decodeList reads the list's entries from either shape.
func decodeList(body []byte) ([]listedModel, error) {
	var wrapped struct {
		Data []listedModel `json:"data"`
	}
	if err := json.Unmarshal(body, &wrapped); err == nil {
		return wrapped.Data, nil
	}
	var bare []listedModel
	if err := json.Unmarshal(body, &bare); err != nil {
		return nil, fmt.Errorf("parsing openai-compatible model list: %w", err)
	}
	return bare, nil
}
