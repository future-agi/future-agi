package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A hop down the caller's own models[] (request_fallback) moves down a model
// fallback chain exactly as a configured model fallback hop and an auto
// runner-up do; no other kind does.
func TestRequestFallbackMovesDownModelChain(t *testing.T) {
	for kind, want := range map[models.AttemptKind]bool{
		models.AttemptPrimary:         false,
		models.AttemptRetry:           false,
		models.AttemptFailover:        false,
		models.AttemptHedge:           false,
		models.AttemptSelector:        false,
		models.AttemptModelFallback:   true,
		models.AttemptAutoFallback:    true,
		models.AttemptRequestFallback: true,
	} {
		if got := movesDownModelChain(kind); got != want {
			t.Errorf("movesDownModelChain(%q) = %v, want %v", kind, got, want)
		}
	}
}

// A BYOK request_fallback hop is priced as a model_fallback hop is: on its own
// model, never on the price the org set for the model asked for, even when
// its host answers naming the id it was sent (which makes the hop's model the
// request's resolved one). The rows are org_card_test.go's "echoing host:
// fallback hop with no org price of its own" and "echoing host: fallback hop
// priced on its own registered id", with the hop's kind varied. A retry of
// the resolved model is the control: it is not a hop to another model, so
// the asked id's org price still covers it.
func TestRequestFallbackHopIsPricedAsAModelFallbackHop(t *testing.T) {
	const (
		haiku = "claude-haiku-4-5"
		down  = "claude-down"
		asked = "anthropic/" + down
	)
	type row struct {
		name   string
		prices map[string]float64 // per million tokens, input and output alike
		cost   string             // 1,000 tokens in and 1,000 out
		source string
	}
	hopRows := []row{
		{name: "no org price of its own: its listed price, not the asked id's org price",
			prices: map[string]float64{asked: 50}, cost: "0.006000", source: "modeldb"}, // claude-haiku-4-5: $1 and $5 a million tokens
		{name: "priced on its own registered id",
			prices: map[string]float64{asked: 50, haiku: 100}, cost: "0.200000", source: "org_custom"},
	}
	run := func(t *testing.T, kind models.AttemptKind, r row) {
		t.Helper()
		db := modeldb.New(map[string]*modeldb.ModelInfo{
			haiku: {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 1e-6, OutputPerToken: 5e-6}},
		}, nil)
		custom := make(map[string]*tenant.CustomPricing, len(r.prices))
		for k, v := range r.prices {
			custom[k] = &tenant.CustomPricing{InputPerMTok: v, OutputPerMTok: v}
		}
		store := tenant.NewStore()
		store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: custom}})
		rc := newRC(asked, 1000, 1000)
		rc.Provider = "anthropic"
		rc.ResolvedModel = haiku // the hop's host answers naming the id it was sent
		rc.Metadata["billing_mode"] = "byok"
		rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
		rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "anthropic", Model: down,
			CredentialType: models.CredentialBYOK, Outcome: models.OutcomeError})
		rc.AddAttempt(models.Attempt{Kind: kind, Provider: "anthropic", Model: haiku,
			CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess})

		New(true, func() *modeldb.ModelDB { return db }, store, nil).ProcessResponse(context.Background(), rc)

		if rc.Metadata["cost"] != r.cost || rc.Metadata["cost_source"] != r.source {
			t.Fatalf("%s hop: cost = %q from %q, want %s from %s (attempts %+v)",
				kind, rc.Metadata["cost"], rc.Metadata["cost_source"], r.cost, r.source, rc.AttemptsSnapshot())
		}
	}
	for _, kind := range []models.AttemptKind{models.AttemptModelFallback, models.AttemptRequestFallback, models.AttemptAutoFallback} {
		for _, r := range hopRows {
			t.Run(string(kind)+"/"+r.name, func(t *testing.T) { run(t, kind, r) })
		}
	}
	t.Run("control: a retry of the resolved model takes the asked id's org price", func(t *testing.T) {
		run(t, models.AttemptRetry, row{prices: map[string]float64{asked: 50}, cost: "0.100000", source: "org_custom"})
	})
}
