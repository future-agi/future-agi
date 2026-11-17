// Package pricing computes token-based span cost, replacing the litellm +
// CustomAIModel lookup that lived in the retired Django OTLP converter
// (futureagi/tracer/utils/otel.py:calculate_cost_from_tokens).
//
// model_prices.json is vendored from
// BerriAI/litellm model_prices_and_context_window.json (MIT license),
// snapshot 2026-07-16. Tiered `*_above_128k_tokens` rates are ported (see
// Cost). Known deviations that were never applied by EITHER implementation —
// Django's calculate_cost_from_tokens passed only prompt/completion token
// counts into litellm's cost_per_token, so none of these ever priced live
// rows: cache-read/cache-creation token costs, reasoning-token costs, and
// per-second/per-image/per-character pricing.
package pricing

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

//go:embed model_prices.json
var embeddedPrices []byte

// above128kThreshold mirrors litellm's generic_cost_per_token tier boundary.
const above128kThreshold = 128_000

type modelPrice struct {
	InputCostPerToken  float64 `json:"input_cost_per_token"`
	OutputCostPerToken float64 `json:"output_cost_per_token"`

	InputCostPerTokenAbove128k  float64 `json:"input_cost_per_token_above_128k_tokens"`
	OutputCostPerTokenAbove128k float64 `json:"output_cost_per_token_above_128k_tokens"`
}

// Table is an immutable model→price map (litellm's
// model_prices_and_context_window.json). Built once at startup; safe for
// concurrent reads.
type Table struct {
	prices map[string]modelPrice

	// providers maps a model key to its litellm_provider ("gpt-4o" →
	// "openai"). ProviderPrefixedCost uses it to tell a real provider prefix
	// from a reseller's or a self-hosted one.
	providers map[string]string

	// Skipped counts price-file entries that failed to decode into
	// modelPrice and were dropped rather than failing the whole load. main
	// logs this so a malformed litellm entry doesn't silently disable
	// pricing for every other model.
	Skipped int
}

// LoadTable parses the price file at path, or the embedded snapshot when
// path is "". Set FI_PRICING_JSON to a mounted, newer copy of
// https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
// to override without rebuilding.
//
// Decoding is tolerant per-entry: the file is first unmarshalled into a
// map of raw JSON, then each entry is decoded individually. An entry that
// fails to decode (e.g. a field with an unexpected JSON type) is skipped —
// not fatal — so one malformed litellm entry can't take down pricing for
// every other model. Only a file that isn't valid JSON at all fails the
// load.
func LoadTable(path string) (*Table, error) {
	raw := embeddedPrices
	if path != "" {
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("pricing: read %s: %w", path, err)
		}
		raw = b
	}
	var rawEntries map[string]json.RawMessage
	if err := json.Unmarshal(raw, &rawEntries); err != nil {
		return nil, fmt.Errorf("pricing: parse price table: %w", err)
	}
	delete(rawEntries, "sample_spec") // litellm's inline documentation entry

	m := make(map[string]modelPrice, len(rawEntries))
	providers := make(map[string]string, len(rawEntries))
	skipped := 0
	for name, entry := range rawEntries {
		var p modelPrice
		if err := json.Unmarshal(entry, &p); err != nil {
			skipped++
			continue
		}
		m[name] = p
		// Decoded on its own so an odd litellm_provider value only turns off
		// the prefix fallback for this entry, never drops its prices.
		var meta struct {
			Provider string `json:"litellm_provider"`
		}
		if json.Unmarshal(entry, &meta) == nil && meta.Provider != "" {
			providers[name] = meta.Provider
		}
	}
	return &Table{prices: m, providers: providers, Skipped: skipped}, nil
}

// Cost prices a call by exact model-key lookup — the same gate Django used
// (`model_cost.get(model)`), so provider-prefixed names ("azure/gpt-4o") only
// match if the SDK sent them that way. ok=false → caller may try custom
// per-org pricing.
//
// Tiered rates mirror litellm's generic_cost_per_token semantics: the tier
// is selected by PROMPT token count. When promptTokens exceeds 128k AND the
// side's above-128k rate is non-zero, ALL tokens on that side price at the
// above-rate (not just the marginal tokens past the threshold); a zero or
// absent above-rate falls back to the base rate.
func (t *Table) Cost(model string, promptTokens, completionTokens int32) (float64, bool) {
	p, ok := t.prices[model]
	if !ok {
		return 0, false
	}

	inRate := p.InputCostPerToken
	outRate := p.OutputCostPerToken
	if promptTokens > above128kThreshold {
		if p.InputCostPerTokenAbove128k != 0 {
			inRate = p.InputCostPerTokenAbove128k
		}
		if p.OutputCostPerTokenAbove128k != 0 {
			outRate = p.OutputCostPerTokenAbove128k
		}
	}

	return float64(promptTokens)*inRate + float64(completionTokens)*outRate, true
}

// ProviderPrefixedCost prices a "provider/model" name that has no exact key,
// such as OpenRouter's "openai/gpt-4o". It drops the prefix and prices the
// bare entry only when that entry's litellm_provider is the prefix ("gpt-4o"
// is an "openai" entry), so a reseller's or self-hosted server's prefix never
// borrows another provider's rate. ok=false when the name has no prefix or
// the bare entry is missing or belongs to another provider.
//
// This is a collector-only fallback; Django priced exact keys only. Pricer
// tries it after the exact table and per-org custom lookups miss.
func (t *Table) ProviderPrefixedCost(model string, promptTokens, completionTokens int32) (float64, bool) {
	prefix, name, ok := strings.Cut(model, "/")
	if !ok || prefix == "" || name == "" {
		return 0, false
	}
	if t.providers[name] != prefix {
		return 0, false
	}
	return t.Cost(name, promptTokens, completionTokens)
}

// openRouterPrefix namespaces OpenRouter's own rates in the litellm table
// ("openrouter/anthropic/claude-haiku-4.5", litellm_provider "openrouter").
const openRouterPrefix = "openrouter/"

// OpenRouterCost prices an OpenRouter model slug such as
// "anthropic/claude-haiku-4.5" from the table's "openrouter/<slug>" entry,
// OpenRouter's own rate for that slug. A slug that already starts with
// "openrouter/" is looked up as given. ok=false when the table has no such
// entry.
//
// Collector-only, like ProviderPrefixedCost; Pricer uses it only for spans
// that came through OpenRouter.
func (t *Table) OpenRouterCost(slug string, promptTokens, completionTokens int32) (float64, bool) {
	if slug == "" {
		return 0, false
	}
	if !strings.HasPrefix(slug, openRouterPrefix) {
		slug = openRouterPrefix + slug
	}
	return t.Cost(slug, promptTokens, completionTokens)
}
