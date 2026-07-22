package pricing

import "context"

// Pricer chains the two token-based pricing sources with Django's exact
// precedence (otel.py:calculate_cost_from_tokens): litellm table by exact
// model key, else per-org CustomAIModel. User-provided cost attributes are
// handled UPSTREAM in adapter.DeriveHotKeys — by the time a caller asks a
// Pricer, the span had no cost attributes.
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
		return p.custom.Cost(ctx, orgID, model, promptTokens, completionTokens)
	}
	return 0, false
}
