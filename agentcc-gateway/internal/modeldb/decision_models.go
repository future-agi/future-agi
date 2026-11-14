package modeldb

import (
	"regexp"
	"strings"
	"time"
)

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
// "decision:" keys and never falls back to a chat model of the same name. It
// tries, in order:
//   - the model as named;
//   - its last path segment ("jev-1.13.0" from "typesafe/jev-1.13.0");
//   - for Jev only, that segment normalized to the version it names, so
//     OpenRouter's "typesafe/jev-1.13" and the dated "typesafe/jev-1.13-20260917"
//     it answers with are priced as jev-1.13.0 (see normalizeJevVersion).
//
// An exact key always wins over normalization, so an operator override on
// "decision:jev-1.13" prices that name on its own.
func (db *ModelDB) DecisionPricing(model string) (*PricingInfo, bool) {
	last := model
	candidates := []string{model}
	if idx := strings.LastIndexByte(model, '/'); idx >= 0 && idx < len(model)-1 {
		last = model[idx+1:]
		candidates = append(candidates, last)
	}
	if normalized, ok := normalizeJevVersion(last); ok {
		candidates = append(candidates, normalized)
	}
	for _, id := range candidates {
		if m, ok := db.models[DecisionKeyPrefix+id]; ok && m.Pricing.HasPricing() {
			return &m.Pricing, true
		}
	}
	return nil, false
}

// jevVersion matches the Jev names OpenRouter uses: "jev-<major>.<minor>",
// optionally with a ".<patch>", optionally with a "-YYYYMMDD" release date.
var jevVersion = regexp.MustCompile(`^jev-(\d+)\.(\d+)(?:\.(\d+))?(?:-(\d{8}))?$`)

// normalizeJevVersion maps a Jev model name to the "jev-<major>.<minor>.<patch>"
// version it names: the date suffix is dropped and a missing patch is ".0", so
// "jev-1.13" and "jev-1.13-20260917" are "jev-1.13.0". A patch that is given is
// kept as is ("jev-1.13.2" stays "jev-1.13.2"), so a name is never priced as
// another version. Anything that is not a Jev version, including a suffix
// that is not a calendar date, is not normalized.
func normalizeJevVersion(name string) (string, bool) {
	m := jevVersion.FindStringSubmatch(name)
	if m == nil {
		return "", false
	}
	if date := m[4]; date != "" {
		if _, err := time.Parse("20060102", date); err != nil {
			return "", false
		}
	}
	patch := m[3]
	if patch == "" {
		patch = "0"
	}
	normalized := "jev-" + m[1] + "." + m[2] + "." + patch
	return normalized, normalized != name
}

// CalculateDecisionCost prices a decision call from the vendor's token counts.
func (db *ModelDB) CalculateDecisionCost(model string, inputTokens, outputTokens int) (float64, bool) {
	pricing, ok := db.DecisionPricing(model)
	if !ok {
		return 0, false
	}
	return pricing.Calculate(inputTokens, outputTokens, CostOptions{}), true
}
