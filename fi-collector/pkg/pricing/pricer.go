package pricing

import "context"

// Pricer chains the token-based pricing sources. The first two keep Django's
// exact precedence (otel.py:calculate_cost_from_tokens): litellm table by
// exact model key, else per-org CustomAIModel. A provider-prefixed name that
// both miss ("openai/gpt-4o") is then priced from the table's bare entry when
// the prefix is that entry's provider (Table.ProviderPrefixedCost); Django had
// no such step. For a span that came through OpenRouter, OpenRouterTokenCost
// tries the table's "openrouter/<slug>" entry just before that step.
// User-provided cost attributes, including a sender's billed cost
// (gen_ai.usage.*_cost), are handled UPSTREAM in adapter.DeriveHotKeys — by
// the time a caller asks a Pricer, the span had no cost attributes.
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
	return p.tokenCost(ctx, orgID, model, promptTokens, completionTokens, false)
}

// OpenRouterTokenCost is TokenCost for a span that came through OpenRouter,
// whose model is an OpenRouter slug ("anthropic/claude-haiku-4.5"). After the
// exact table key and the org's own price miss, OpenRouter's rate for the
// slug (the table's "openrouter/<slug>" entry) is tried before the bare
// provider entry, so a slug like anthropic/claude-haiku-4.5, which has no
// same-provider bare key, is still priced.
func (p *Pricer) OpenRouterTokenCost(
	ctx context.Context, orgID, model string, promptTokens, completionTokens int32,
) (float64, bool) {
	return p.tokenCost(ctx, orgID, model, promptTokens, completionTokens, true)
}

func (p *Pricer) tokenCost(
	ctx context.Context, orgID, model string, promptTokens, completionTokens int32, viaOpenRouter bool,
) (float64, bool) {
	if model == "" || (promptTokens <= 0 && completionTokens <= 0) {
		return 0, false
	}
	if c, ok := p.table.Cost(model, promptTokens, completionTokens); ok {
		return c, true
	}
	if p.custom != nil && orgID != "" {
		// An org's own price for the exact name beats the prefix fallbacks.
		if c, ok := p.custom.Cost(ctx, orgID, model, promptTokens, completionTokens); ok {
			return c, true
		}
	}
	if viaOpenRouter {
		if c, ok := p.table.OpenRouterCost(model, promptTokens, completionTokens); ok {
			return c, true
		}
	}
	return p.table.ProviderPrefixedCost(model, promptTokens, completionTokens)
}
