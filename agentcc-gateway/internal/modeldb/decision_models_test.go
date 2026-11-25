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

// OpenRouter names Jev "typesafe/jev-1.13" and answers with a dated model
// such as "typesafe/jev-1.13-20260917"; both are the priced jev-1.13.0.
func TestDecisionPricingOpenRouterJevNames(t *testing.T) {
	db := New(BundledModels, nil)
	for _, model := range []string{
		"typesafe/jev-1.13",
		"typesafe/jev-1.14",
		"typesafe/jev-1.13-20260917",
		"typesafe/jev-1.14-20261015",
		"typesafe/jev-1.13.0-20260917",
		"openrouter/typesafe/jev-1.13",
		"~typesafe/jev-latest",
		"typesafe/jev-1.13.0",
		"jev-1.13",
	} {
		cost, ok := db.CalculateDecisionCost(model, 1_000_000, 500)
		if !ok || math.Abs(cost-0.042) > 1e-12 {
			t.Errorf("%s: cost = %g, %v; want 0.042, true", model, cost, ok)
		}
	}
}

// Only the known Jev shapes are normalized. An unknown version, an unknown
// patch, a malformed date or another model stays unpriced: never free and
// never priced as another version.
func TestDecisionPricingUnknownJevVersionsStayUnpriced(t *testing.T) {
	db := New(BundledModels, nil)
	for _, model := range []string{
		"jev-9.9",
		"typesafe/jev-9.9",
		"typesafe/jev-9.9-20270101",
		"jev-1.13.2",
		"jev-1.13-2026",
		"jev-1.13-20260917-beta",
		"jev-1.13-20261399",
		"xjev-1.13",
		"jev-1.13/",
		"gpt-6-luna-20260917",
	} {
		if cost, ok := db.CalculateDecisionCost(model, 1_000_000, 500); ok || cost != 0 {
			t.Errorf("%s: cost = %g, %v; want 0, false", model, cost, ok)
		}
	}
}

// An exact decision key beats normalization, so an operator can price one
// OpenRouter name apart from the bundled version it normalizes to.
func TestDecisionPricingExactKeyBeatsJevNormalization(t *testing.T) {
	p := 0.5 / 1_000_000
	db := New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-1.13": {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &p}},
	})
	if cost, ok := db.CalculateDecisionCost("typesafe/jev-1.13", 1_000_000, 0); !ok || math.Abs(cost-0.5) > 1e-9 {
		t.Errorf("typesafe/jev-1.13 = %g, %v; want the 0.5 override", cost, ok)
	}
	// The dated name is a release of jev-1.13, so the jev-1.13 override prices
	// it too: the cost plugin looks up the dated resolved name first, and the
	// override must not lose to the bundled jev-1.13.0 there.
	if cost, ok := db.CalculateDecisionCost("typesafe/jev-1.13-20260917", 1_000_000, 0); !ok || math.Abs(cost-0.5) > 1e-9 {
		t.Errorf("typesafe/jev-1.13-20260917 = %g, %v; want the 0.5 override", cost, ok)
	}
	// Another version keeps the bundled price.
	if cost, ok := db.CalculateDecisionCost("typesafe/jev-1.14-20261015", 1_000_000, 0); !ok || math.Abs(cost-0.042) > 1e-9 {
		t.Errorf("typesafe/jev-1.14-20261015 = %g, %v; want 0.042", cost, ok)
	}
	// An override on the dated name itself beats the undated one.
	q := 0.7 / 1_000_000
	db = New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-1.13":          {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &p}},
		DecisionKeyPrefix + "jev-1.13-20260917": {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &q}},
	})
	if cost, ok := db.CalculateDecisionCost("typesafe/jev-1.13-20260917", 1_000_000, 0); !ok || math.Abs(cost-0.7) > 1e-9 {
		t.Errorf("typesafe/jev-1.13-20260917 = %g, %v; want the 0.7 dated override", cost, ok)
	}
}

// A Jev name never borrows a chat-namespace price, even when a chat entry of
// the same name exists, as litellm's "typesafe/jev-*" evaluation entries do.
func TestDecisionPricingJevNeverUsesChatEntries(t *testing.T) {
	bundled := map[string]*ModelInfo{
		"typesafe/jev-9.9": {Provider: "typesafe", Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 7.0 / 1_000_000}},
		"jev-9.9":          {Provider: "typesafe", Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 7.0 / 1_000_000}},
		"jev-9.9.0":        {Provider: "typesafe", Mode: ModeChat, Pricing: PricingInfo{InputPerToken: 7.0 / 1_000_000}},
	}
	addDecisionModels(bundled)
	db := New(bundled, nil)
	for _, model := range []string{"typesafe/jev-9.9", "jev-9.9", "typesafe/jev-9.9-20270101"} {
		if cost, ok := db.CalculateDecisionCost(model, 1_000_000, 0); ok {
			t.Errorf("%s priced %g from a chat entry", model, cost)
		}
	}
}

// A model known by several names is priced from an exact key for any of them
// before any name is normalized, so an override on a later, full name is not
// shadowed by an earlier dated name normalizing to a bundled version.
func TestDecisionPricingAnyTriesExactNamesBeforeNormalizing(t *testing.T) {
	dated, upstream, qualified := "typesafe/jev-1.13-20260917", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"
	names := []string{dated, upstream, qualified}
	p := 0.5 / 1_000_000
	price := func(db *ModelDB, names ...string) float64 {
		t.Helper()
		pricing, ok := db.DecisionPricingAny(names...)
		if !ok {
			t.Fatalf("%v: no price", names)
		}
		return pricing.InputPerMTok()
	}

	if got := price(New(BundledModels, nil), names...); math.Abs(got-0.042) > 1e-9 {
		t.Errorf("no overrides: %g, want the bundled 0.042", got)
	}
	for _, key := range []string{upstream, qualified, "jev-1.13", dated} {
		db := New(BundledModels, map[string]ModelOverride{
			DecisionKeyPrefix + key: {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &p}},
		})
		if got := price(db, names...); math.Abs(got-0.5) > 1e-9 {
			t.Errorf("override on %q: %g, want 0.5", key, got)
		}
		// The listing prices the same entry from its upstream and listed names.
		if got := price(db, upstream, qualified); key != dated && math.Abs(got-0.5) > 1e-9 {
			t.Errorf("listing with override on %q: %g, want 0.5", key, got)
		}
	}

	// Earlier names win among exact keys.
	q := 0.7 / 1_000_000
	db := New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + upstream:  {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &p}},
		DecisionKeyPrefix + qualified: {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &q}},
	})
	if got := price(db, names...); math.Abs(got-0.5) > 1e-9 {
		t.Errorf("both overrides: %g, want the upstream name's 0.5", got)
	}

	// An operator's price for any name beats a bundled price for another: the
	// resolved version's override beats the bundled alias a jev-latest call was
	// sent as, and an alias override beats the bundled exact resolved version.
	latestCall := []string{"typesafe/jev-1.14-20261015", "~typesafe/jev-latest", "openrouter/~typesafe/jev-latest"}
	for _, tt := range []struct {
		key   string
		names []string
	}{
		{"jev-1.14.0", latestCall},
		{"jev-latest", latestCall},
		{"jev-latest", []string{"jev-1.13.0", "jev-latest", "typesafe/jev-latest"}},
		{qualified, []string{"typesafe/jev-1.13.0", upstream, qualified}},
	} {
		db := New(BundledModels, map[string]ModelOverride{
			DecisionKeyPrefix + tt.key: {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &p}},
		})
		if got := price(db, tt.names...); math.Abs(got-0.5) > 1e-9 {
			t.Errorf("override on %q for %v: %g, want 0.5", tt.key, tt.names, got)
		}
	}
	// Without overrides those calls keep their bundled prices.
	if got := price(New(BundledModels, nil), latestCall...); math.Abs(got-0.042) > 1e-9 {
		t.Errorf("jev-latest call without overrides: %g, want 0.042", got)
	}
	// A mode-only override sets no price, so it does not outrank bundled ones.
	db = New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-latest": {Mode: string(ModeDecision)},
	})
	if got := price(db, latestCall...); math.Abs(got-0.042) > 1e-9 {
		t.Errorf("mode-only override: %g, want 0.042", got)
	}

	// An unknown version stays unpriced under every name.
	if pricing, ok := New(BundledModels, nil).DecisionPricingAny("typesafe/jev-9.9-20270101", "typesafe/jev-9.9", "openrouter/typesafe/jev-9.9"); ok {
		t.Errorf("unknown Jev version priced %+v", pricing)
	}
	if _, ok := New(BundledModels, nil).DecisionPricingAny(); ok {
		t.Error("no names priced")
	}
}

// Within a tier every full name is tried before any last segment, so a
// generic override on the version's short name never outranks a provider-
// specific override on a later full name, whatever form the answer's name has.
func TestDecisionPricingAnyTriesFullNamesBeforeLastSegments(t *testing.T) {
	a, b := 0.3/1_000_000, 0.5/1_000_000
	db := New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-1.13.0":                   {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &a}},
		DecisionKeyPrefix + "openrouter/typesafe/jev-1.13": {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &b}},
	})
	for _, names := range [][]string{
		{"typesafe/jev-1.13.0", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"},
		{"typesafe/jev-1.13-20260917", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"},
		{"typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"}, // the listing
	} {
		pricing, ok := db.DecisionPricingAny(names...)
		if !ok || math.Abs(pricing.InputPerMTok()-0.5) > 1e-9 {
			t.Errorf("%v: %+v, want the qualified override 0.5", names, pricing)
		}
	}
	// A last segment is still an exact candidate: with only the generic
	// override, every form is charged it.
	db = New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-1.13.0": {Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: &a}},
	})
	if pricing, ok := db.DecisionPricingAny("typesafe/jev-1.13.0", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"); !ok || math.Abs(pricing.InputPerMTok()-0.3) > 1e-9 {
		t.Errorf("generic override only: %+v, want 0.3", pricing)
	}
}

// Pins a known limitation for the owner (see DecisionPricingAny): with
// overrides on two short names of one model, the charge follows the form of
// the answer. Changing this must be a deliberate decision.
func TestDecisionPricingTwoShortNameOverridesFollowTheAnswersForm(t *testing.T) {
	a, b, latest, v14 := 0.5/1_000_000, 0.6/1_000_000, 0.3/1_000_000, 0.4/1_000_000
	override := func(price *float64) ModelOverride {
		return ModelOverride{Mode: string(ModeDecision), Pricing: &PricingOverride{InputPerToken: price}}
	}
	db := New(BundledModels, map[string]ModelOverride{
		DecisionKeyPrefix + "jev-1.13":   override(&a),
		DecisionKeyPrefix + "jev-1.13.0": override(&b),
		DecisionKeyPrefix + "jev-latest": override(&latest),
		DecisionKeyPrefix + "jev-1.14.0": override(&v14),
	})
	price := func(names ...string) float64 {
		t.Helper()
		pricing, ok := db.DecisionPricingAny(names...)
		if !ok {
			t.Fatalf("%v unpriced", names)
		}
		return pricing.InputPerMTok()
	}
	for _, tt := range []struct {
		name  string
		names []string
		want  float64
	}{
		// The charge: resolved, upstream, provider-qualified.
		{"dated answer", []string{"typesafe/jev-1.13-20260917", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"}, 0.5},
		{"undated answer", []string{"typesafe/jev-1.13.0", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"}, 0.6},
		// The listing: upstream, then the listed id.
		{"listing", []string{"typesafe/jev-1.13", "openrouter/typesafe/jev-1.13"}, 0.5},
		{"alias, dated answer", []string{"typesafe/jev-1.14-20261015", "~typesafe/jev-latest", "openrouter/~typesafe/jev-latest"}, 0.3},
		{"alias, undated answer", []string{"typesafe/jev-1.14.0", "~typesafe/jev-latest", "openrouter/~typesafe/jev-latest"}, 0.4},
		{"alias listing", []string{"~typesafe/jev-latest", "openrouter/~typesafe/jev-latest"}, 0.3},
	} {
		if got := price(tt.names...); math.Abs(got-tt.want) > 1e-9 {
			t.Errorf("%s: %g/Mtok, want %g", tt.name, got, tt.want)
		}
	}
}
