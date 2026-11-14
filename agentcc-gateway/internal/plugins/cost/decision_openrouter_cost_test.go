package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// Jev called through OpenRouter is priced at the vendor rate under the names
// OpenRouter uses: the caller's, the upstream's and the dated one it answers
// with. An unknown Jev version stays unpriced.
func TestCostPlugin_DecisionOpenRouterJev(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tt := range []struct {
		name, model, upstream, resolved string
	}{
		{"jev-1.13 dated", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"jev-1.14 dated", "openrouter/typesafe/jev-1.14", "typesafe/jev-1.14", "typesafe/jev-1.14-20261015"},
		{"jev-latest", "openrouter/~typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			rc := decisionRC(tt.model, tt.upstream, tt.resolved, 1_000_000, 5)
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != "0.042000" {
				t.Fatalf("cost = %q, want 0.042000", got)
			}
			if src, ok := rc.Metadata["cost_source"]; ok {
				t.Fatalf("cost_source = %q, want none", src)
			}
		})
	}

	t.Run("unknown version", func(t *testing.T) {
		rc := decisionRC("openrouter/typesafe/jev-9.9", "typesafe/jev-9.9", "typesafe/jev-9.9-20270101", 1_000_000, 5)
		p.ProcessResponse(context.Background(), rc)
		if got, ok := rc.Metadata["cost"]; ok {
			t.Fatalf("unknown Jev version got cost %q", got)
		}
		if got := rc.Metadata["cost_source"]; got != "unpriced" {
			t.Fatalf("cost_source = %q, want unpriced", got)
		}
	})
}

// An operator override on the name OpenRouter is called with prices the call
// even though the cost plugin looks up the dated resolved name first.
func TestCostPlugin_DecisionOpenRouterJevOverride(t *testing.T) {
	price := 0.5 / 1_000_000
	db := modeldb.New(modeldb.BundledModels, map[string]modeldb.ModelOverride{
		modeldb.DecisionKeyPrefix + "jev-1.13": {Mode: string(modeldb.ModeDecision), Pricing: &modeldb.PricingOverride{InputPerToken: &price}},
	})
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	rc := decisionRC("openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917", 1_000_000, 5)
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "0.500000" {
		t.Fatalf("cost = %q, want the 0.500000 override", got)
	}
}

// An operator override keyed on any name the call goes by, including the full
// OpenRouter names, prices the call. Exact names are tried for every name
// before any Jev name is normalized, so the dated resolved name normalizing to
// the bundled "jev-1.13.0" or "jev-1.14.0" never shadows an override.
func TestCostPlugin_DecisionOpenRouterJevFullNameOverride(t *testing.T) {
	price := 0.5 / 1_000_000
	for _, tt := range []struct {
		name, key, model, upstream, resolved string
	}{
		{"upstream name", "typesafe/jev-1.13", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"provider-qualified name", "openrouter/typesafe/jev-1.13", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"jev-latest answered as dated jev-1.14", "jev-latest", "openrouter/~typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
		{"short name", "jev-1.13", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		// An override on the version a jev-latest call resolves to beats the
		// bundled jev-latest price of the alias it was sent as.
		{"resolved version under jev-latest", "jev-1.14.0", "openrouter/~typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
		// An override on the full name beats the bundled price of an exact
		// resolved name.
		{"provider-qualified name, exact resolved", "openrouter/typesafe/jev-1.13", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13.0"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			db := modeldb.New(modeldb.BundledModels, map[string]modeldb.ModelOverride{
				modeldb.DecisionKeyPrefix + tt.key: {Mode: string(modeldb.ModeDecision), Pricing: &modeldb.PricingOverride{InputPerToken: &price}},
			})
			p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
			rc := decisionRC(tt.model, tt.upstream, tt.resolved, 1_000_000, 5)
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != "0.500000" {
				t.Fatalf("override on %q: cost = %q, want 0.500000", modeldb.DecisionKeyPrefix+tt.key, got)
			}
		})
	}
}
