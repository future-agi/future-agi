package modeldb

import (
	"math"
	"testing"
)

func TestDecisionModelsAreBundled(t *testing.T) {
	db := New(BundledModels, nil)
	for model, want := range map[string]float64{
		"jev-1.13.0":            0.042,
		"jev-latest":            0.042,
		"jev-1.14.0":            0.042,
		"pplx-decider-v1.1-27b": 0.02,
		"pplx-decider-v1-27b":   0.02,
		"gpt-6-luna":            0.10,
	} {
		info, ok := db.Get(DecisionKeyPrefix + model)
		if !ok || info.Mode != ModeDecision || info.Provider == "" {
			t.Fatalf("%s: info = %+v", model, info)
		}
		if got := info.Pricing.InputPerMTok(); math.Abs(got-want) > 1e-12 || info.Pricing.OutputPerToken != 0 {
			t.Errorf("%s: input %g/Mtok output %g, want %g and 0", model, got, info.Pricing.OutputPerToken, want)
		}
		cost, ok := db.CalculateDecisionCost(model, 1_000_000, 500)
		if !ok || math.Abs(cost-want) > 1e-12 {
			t.Errorf("%s: cost = %g, %v", model, cost, ok)
		}
	}
}

// A decision model priced as one never borrows a chat price, and a chat model
// of the same name keeps its own: gpt-6-luna is both.
func TestDecisionPricingIsItsOwnNamespace(t *testing.T) {
	bundled := map[string]*ModelInfo{
		"gpt-6-luna": {Provider: "openai", Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 5.0 / 1_000_000}},
		"jev-chat":   {Provider: "x", Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 9.0 / 1_000_000}},
	}
	addDecisionModels(bundled)
	db := New(bundled, nil)

	if cost, _ := db.CalculateCost("gpt-6-luna", 1_000_000, 0, CostOptions{}); math.Abs(cost-5.0) > 1e-9 {
		t.Errorf("chat gpt-6-luna = %g, want its chat price 5", cost)
	}
	if cost, _ := db.CalculateDecisionCost("gpt-6-luna", 1_000_000, 0); math.Abs(cost-0.10) > 1e-9 {
		t.Errorf("decision gpt-6-luna = %g, want 0.10", cost)
	}
	// "openai/gpt-6-luna" resolves under decision: by its last segment.
	if cost, ok := db.CalculateDecisionCost("openai/gpt-6-luna", 1_000_000, 0); !ok || math.Abs(cost-0.10) > 1e-9 {
		t.Errorf("prefixed decision gpt-6-luna = %g, %v", cost, ok)
	}
	// A chat-only model is not a decision model, prefixed or not.
	for _, model := range []string{"jev-chat", "x/jev-chat"} {
		if _, ok := db.CalculateDecisionCost(model, 1_000_000, 0); ok {
			t.Errorf("%s priced as a decision from its chat entry", model)
		}
	}
}

// An operator can price a new decision model with a model_database override.
func TestDecisionPricingOverride(t *testing.T) {
	price := 0.24 / 1_000_000
	db := New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "clef-1": {Provider: "cloudflare", Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &price}},
	})
	if cost, ok := db.CalculateDecisionCost("clef-1", 1_000_000, 0); !ok || math.Abs(cost-0.24) > 1e-9 {
		t.Fatalf("cost = %g, %v", cost, ok)
	}
}
