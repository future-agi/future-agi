// Package pricing computes token-based span cost, replacing the litellm +
// CustomAIModel lookup that lived in the retired Django OTLP converter
// (futureagi/tracer/utils/otel.py:calculate_cost_from_tokens).
//
// model_prices.json is vendored from
// BerriAI/litellm model_prices_and_context_window.json (MIT license),
// snapshot 2026-07-16. Known deviation: litellm applies tiered
// `*_above_128k_tokens` rates for some models; we apply only the flat base
// rate (input_cost_per_token / output_cost_per_token) and do not implement
// litellm's cost_per_token tiering.
package pricing

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
)

//go:embed model_prices.json
var embeddedPrices []byte

type modelPrice struct {
	InputCostPerToken  float64 `json:"input_cost_per_token"`
	OutputCostPerToken float64 `json:"output_cost_per_token"`
}

// Table is an immutable model→price map (litellm's
// model_prices_and_context_window.json). Built once at startup; safe for
// concurrent reads.
type Table struct {
	prices map[string]modelPrice
}

// LoadTable parses the price file at path, or the embedded snapshot when
// path is "". Set FI_PRICING_JSON to a mounted, newer copy of
// https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
// to override without rebuilding.
func LoadTable(path string) (*Table, error) {
	raw := embeddedPrices
	if path != "" {
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("pricing: read %s: %w", path, err)
		}
		raw = b
	}
	var m map[string]modelPrice
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("pricing: parse price table: %w", err)
	}
	delete(m, "sample_spec") // litellm's inline documentation entry
	return &Table{prices: m}, nil
}

// Cost prices a call by exact model-key lookup — the same gate Django used
// (`model_cost.get(model)`), so provider-prefixed names ("azure/gpt-4o") only
// match if the SDK sent them that way. ok=false → caller may try custom
// per-org pricing.
func (t *Table) Cost(model string, promptTokens, completionTokens int32) (float64, bool) {
	p, ok := t.prices[model]
	if !ok {
		return 0, false
	}
	return float64(promptTokens)*p.InputCostPerToken +
		float64(completionTokens)*p.OutputCostPerToken, true
}
