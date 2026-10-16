package modeldb

import "strings"

// DecisionKeyPrefix namespaces decision-model prices. A decision model can
// share its name with a chat model (gpt-6-luna is both) at a different price,
// so the two never look each other up.
const DecisionKeyPrefix = "decision:"

// decisionModels are the decision models with a vendor-confirmed price, in
// USD per million input tokens. Every one of them bills nothing for output.
var decisionModels = map[string]struct {
	provider     string
	inputPerMTok float64
}{
	"jev-1.13.0":            {"typesafe", 0.042},
	"jev-1.14.0":            {"typesafe", 0.042},
	"jev-latest":            {"typesafe", 0.042},
	"pplx-decider-v1.1-27b": {"perplexity", 0.02},
	"pplx-decider-v1-27b":   {"perplexity", 0.02},
	"gpt-6-luna":            {"openai", 0.10},
}

// addDecisionModels adds the decision models to bundled under their
// "decision:" keys.
func addDecisionModels(bundled map[string]*ModelInfo) {
	for id, m := range decisionModels {
		key := DecisionKeyPrefix + id
		bundled[key] = &ModelInfo{
			ID:       key,
			Provider: m.provider,
			Mode:     ModeDecision,
			Pricing:  PricingInfo{InputPerToken: m.inputPerMTok / 1_000_000},
		}
	}
}

// DecisionPricing returns a decision model's price. It looks only under
// "decision:" keys, first for the model as named and then for its last path
// segment ("typesafe/jev-1.13.0" from OpenRouter), and never falls back to a
// chat model of the same name.
func (db *ModelDB) DecisionPricing(model string) (*PricingInfo, bool) {
	candidates := []string{model}
	if idx := strings.LastIndexByte(model, '/'); idx >= 0 && idx < len(model)-1 {
		candidates = append(candidates, model[idx+1:])
	}
	for _, id := range candidates {
		if m, ok := db.models[DecisionKeyPrefix+id]; ok && m.Pricing.HasPricing() {
			return &m.Pricing, true
		}
	}
	return nil, false
}

// CalculateDecisionCost prices a decision call from the vendor's token counts.
func (db *ModelDB) CalculateDecisionCost(model string, inputTokens, outputTokens int) (float64, bool) {
	pricing, ok := db.DecisionPricing(model)
	if !ok {
		return 0, false
	}
	return pricing.Calculate(inputTokens, outputTokens, CostOptions{}), true
}
