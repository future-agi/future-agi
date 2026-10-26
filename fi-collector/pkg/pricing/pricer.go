package pricing

import "context"

// Pricer chains the token-based pricing sources. The first two keep Django's
// exact precedence (otel.py:calculate_cost_from_tokens): litellm table by
// exact model key, else per-org CustomAIModel. A provider-prefixed name that
// both miss ("openai/gpt-4o") is then priced from the table's bare entry when
// the prefix is that entry's provider (Table.ProviderPrefixedCost); Django had
// no such step. User-provided cost attributes, including a sender's billed
// cost (gen_ai.usage.*_cost), are handled UPSTREAM in adapter.DeriveHotKeys —
// by the time a caller asks a Pricer, the span had no cost attributes.
type Pricer struct {
	table  *Table
	custom *CustomPricing // nil when the collector runs without PG
}

func New(table *Table, custom *CustomPricing) *Pricer {
	return &Pricer{table: table, custom: custom}
}

// TokenCost returns (cost, true) when priceable. Gate mirrors Django:
// `if model and (prompt_tokens > 0 or completion_tokens > 0)`.
func (p *Pricer) TokenCost(
	ctx context.Context, orgID, model string, promptTokens, completionTokens int32,
) (float64, bool) {
	if model == "" || (promptTokens <= 0 && completionTokens <= 0) {
		return 0, false
	}
	if c, ok := p.table.Cost(model, promptTokens, completionTokens); ok {
		return c, true
	}
	if p.custom != nil && orgID != "" {
		// An org's own price for the exact name beats the prefix fallback.
		if c, ok := p.custom.Cost(ctx, orgID, model, promptTokens, completionTokens); ok {
			return c, true
		}
	}
	return p.table.ProviderPrefixedCost(model, promptTokens, completionTokens)
}
