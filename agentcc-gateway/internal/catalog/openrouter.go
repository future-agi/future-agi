package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strconv"
	"time"
)

// openRouterSnapshot is OpenRouter's /api/v1/models, trimmed to the fields
// ParseOpenRouter reads, with the time it was fetched. It lets a managed
// OpenRouter provider route and price models before the first live fetch,
// and when OpenRouter is unreachable. Refresh it with
// scripts/update-openrouter-catalog.sh.
//
//go:embed openrouter_snapshot.json
var openRouterSnapshot []byte

// BundledOpenRouter parses the snapshot bundled with the gateway, and
// returns when it was fetched (zero when the file does not say).
func BundledOpenRouter() (map[string]*Model, time.Time, error) {
	models, err := ParseOpenRouter(openRouterSnapshot)
	if err != nil {
		return nil, time.Time{}, err
	}
	var head struct {
		FetchedAt time.Time `json:"fetched_at"`
	}
	_ = json.Unmarshal(openRouterSnapshot, &head) // parsed above: cannot fail
	return models, head.FetchedAt, nil
}

type openRouterModels struct {
	Data []struct {
		ID            string `json:"id"`
		CanonicalSlug string `json:"canonical_slug"`
		Name          string `json:"name"`
		Description   string `json:"description"`
		Created       int64  `json:"created"`
		ContextLength int    `json:"context_length"`
		Architecture  struct {
			Modality         string   `json:"modality"`
			InputModalities  []string `json:"input_modalities"`
			OutputModalities []string `json:"output_modalities"`
			Tokenizer        string   `json:"tokenizer"`
			InstructType     *string  `json:"instruct_type"`
		} `json:"architecture"`
		Pricing     map[string]any `json:"pricing"`
		TopProvider struct {
			MaxCompletionTokens *int `json:"max_completion_tokens"`
		} `json:"top_provider"`
		SupportedParameters []string        `json:"supported_parameters"`
		ExpirationDate      *string         `json:"expiration_date"`
		Benchmarks          json.RawMessage `json:"benchmarks"`
	} `json:"data"`
}

// ParseOpenRouter reads an OpenRouter /api/v1/models response.
func ParseOpenRouter(body []byte) (map[string]*Model, error) {
	var resp openRouterModels
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("parsing openrouter models: %w", err)
	}
	if len(resp.Data) == 0 {
		return nil, fmt.Errorf("openrouter models: empty catalog")
	}
	out := make(map[string]*Model, len(resp.Data))
	for _, d := range resp.Data {
		if d.ID == "" {
			continue
		}
		m := &Model{
			ID:                  d.ID,
			CanonicalSlug:       d.CanonicalSlug,
			Name:                d.Name,
			Description:         d.Description,
			Created:             d.Created,
			ContextLength:       d.ContextLength,
			Modality:            d.Architecture.Modality,
			InputModalities:     d.Architecture.InputModalities,
			OutputModalities:    d.Architecture.OutputModalities,
			Tokenizer:           d.Architecture.Tokenizer,
			SupportedParameters: d.SupportedParameters,
		}
		if d.Architecture.InstructType != nil {
			m.InstructType = *d.Architecture.InstructType
		}
		if d.TopProvider.MaxCompletionTokens != nil {
			m.MaxOutputTokens = *d.TopProvider.MaxCompletionTokens
		}
		if d.ExpirationDate != nil {
			m.ExpirationDate = *d.ExpirationDate
		}
		if len(d.Benchmarks) > 0 && string(d.Benchmarks) != "null" {
			m.Benchmarks = d.Benchmarks
		}
		m.Prices, m.PriceTiers, m.DynamicPrice = parsePricing(d.Pricing)
		m.syncPrices()
		m.Preview = isPreview(m.ID, m.Name)
		out[d.ID] = m
	}
	return out, nil
}

// pricingKeys maps OpenRouter's pricing fields to the price card.
var pricingKeys = map[string]func(*Prices) *float64{
	"prompt":             func(p *Prices) *float64 { return &p.Prompt },
	"completion":         func(p *Prices) *float64 { return &p.Completion },
	"input_cache_read":   func(p *Prices) *float64 { return &p.CacheRead },
	"input_cache_write":  func(p *Prices) *float64 { return &p.CacheWrite },
	"request":            func(p *Prices) *float64 { return &p.Request },
	"image":              func(p *Prices) *float64 { return &p.Image },
	"web_search":         func(p *Prices) *float64 { return &p.WebSearch },
	"internal_reasoning": func(p *Prices) *float64 { return &p.Reasoning },
}

// ParsePricing reads an OpenRouter-shaped pricing object (decimal strings or
// numbers, USD per token or unit) the way ParseOpenRouter does. listed
// reports whether it names any OpenRouter price line at all; another
// shape ({"input": 0.88} per million) lists none.
func ParsePricing(raw map[string]any) (base Prices, tiers []PriceTier, dynamic, listed bool) {
	for key := range pricingKeys {
		if _, ok := number(raw[key]); ok {
			listed = true
			break
		}
	}
	base, tiers, dynamic = parsePricing(raw)
	return base, tiers, dynamic, listed
}

// parsePricing reads an OpenRouter pricing object. A negative price on any
// line means only the upstream's reported cost can price the call: the
// price is dynamic and the card left empty. "overrides" are tiers that
// replace the listed lines from a prompt size on. "discount" (OpenRouter's
// data-sharing discount) is never used.
func parsePricing(raw map[string]any) (base Prices, tiers []PriceTier, dynamic bool) {
	base, dynamic = readPrices(raw, Prices{})
	overrides, _ := raw["overrides"].([]any)
	for _, o := range overrides {
		obj, ok := o.(map[string]any)
		if !ok {
			continue
		}
		minTokens, ok := number(obj["min_prompt_tokens"])
		if !ok || minTokens <= 0 {
			continue
		}
		p, neg := readPrices(obj, base)
		dynamic = dynamic || neg
		tiers = append(tiers, PriceTier{MinPromptTokens: int(minTokens), Prices: p})
	}
	if dynamic {
		return Prices{}, nil, true
	}
	return base, tiers, false
}

// readPrices overlays the pricing lines raw lists on from; negative reports
// whether any listed line is negative.
func readPrices(raw map[string]any, from Prices) (p Prices, negative bool) {
	p = from
	for key, field := range pricingKeys {
		if v, ok := number(raw[key]); ok {
			if v < 0 {
				negative = true
			}
			*field(&p) = v
		}
	}
	return p, negative
}

// number reads an OpenRouter number, which is usually a decimal string
// ("0.0000025") and sometimes a JSON number.
func number(v any) (float64, bool) {
	switch x := v.(type) {
	case string:
		f, err := strconv.ParseFloat(x, 64)
		return f, err == nil
	case float64:
		return x, true
	default:
		return 0, false
	}
}
