package cost

// MIXED-HOP-GUARD-1 round 3: a request that sent more attempts than its
// record holds (models.MaxAttempts) still bills the attempt that served it
// on that attempt's own card, and every failed attempt that carries a charge
// exactly. This file uses only APIs that exist at 4e38b7961b (the round-2
// head), where the attempt past the record was dropped and the answer was
// priced on the last failure's card.

import (
	"context"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const pastRecordServing = "openai/gpt-6-sol"

var (
	// pastRecordServingCard prices 1000 prompt and 500 completion tokens at
	// 17,500 upstream micros, 18,375 billed at the plugin's 5% markup.
	pastRecordServingCard = models.PriceCard{Prompt: 5e-6, Completion: 25e-6, Source: models.PriceSourceCatalog}
	// pastRecordFailedCard is the failed models' card, far cheaper.
	pastRecordFailedCard = models.PriceCard{Prompt: 0.03e-6, Completion: 0.13e-6, Source: models.PriceSourceCatalog}
)

// pastRecordAttempt is a managed attempt admitted at card, as startAttempt
// records one; charged gives a failure the 0.0001 USD its upstream reported
// before it failed (100 upstream micros, 105 billed).
func pastRecordAttempt(model string, card models.PriceCard, outcome models.AttemptOutcome, charged bool) models.Attempt {
	a := models.Attempt{Kind: models.AttemptModelFallback, Provider: "openrouter", Model: model, CredentialType: models.CredentialManaged,
		Outcome: outcome, Pricing: models.NewAttemptPricing(&models.BillingState{ReservePrice: &card})}
	if charged {
		cost := 0.0001
		a.PromptTokens = 10
		a.Usage = &models.Usage{PromptTokens: 10, Cost: &cost}
	}
	return a
}

func TestFinalizeBillsTheServingAttemptPastTheRecord(t *testing.T) {
	cases := []struct {
		name            string
		failed          int
		charged         func(i int) bool
		billed, upstrem int64
	}{
		// Control: the serving attempt fits in the record.
		{"15 chargeless failures", models.MaxAttempts - 1, func(int) bool { return false }, 18375, 17500},
		{"16 chargeless failures", models.MaxAttempts, func(int) bool { return false }, 18375, 17500},
		{"20 chargeless failures", models.MaxAttempts + 4, func(int) bool { return false }, 18375, 17500},
		// Ten of twenty failures charged: each is billed 105, the answer 18,375.
		{"20 failures, the odd ones charged", models.MaxAttempts + 4, func(i int) bool { return i%2 == 1 }, 18375 + 10*105, 17500 + 10*100},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p := billingPlugin(nil)
			rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
			rc.Model = pastRecordServing
			charged := 0
			for i := range c.failed {
				if c.charged(i) {
					charged++
				}
				addAttempt(rc, pastRecordAttempt(fmt.Sprintf("qwen/failed-%d", i), pastRecordFailedCard, models.OutcomeError, c.charged(i)))
			}
			addAttempt(rc, pastRecordAttempt(pastRecordServing, pastRecordServingCard, models.OutcomeSuccess, false))

			p.ProcessResponse(context.Background(), rc)

			if b := rc.Billing; b.BilledMicros != c.billed || b.UpstreamMicros != c.upstrem {
				t.Fatalf("billed %d on %d upstream; want %d on %d: the serving attempt on its own card plus every charged failure",
					b.BilledMicros, b.UpstreamMicros, c.billed, c.upstrem)
			}
			snap := rc.AttemptsSnapshot()
			if len(snap) > models.MaxAttempts {
				t.Fatalf("recorded %d attempts; the record holds %d", len(snap), models.MaxAttempts)
			}
			last := snap[len(snap)-1]
			if last.Model != pastRecordServing || !last.Billable || last.BilledCostMicros != 18375 || last.UpstreamCostMicros != 17500 {
				t.Fatalf("last recorded attempt %+v; want the serving attempt billed 18375 on 17500", last)
			}
			billable := 0
			for _, a := range snap[:len(snap)-1] {
				switch {
				case a.Usage != nil && (!a.Billable || a.BilledCostMicros != 105):
					t.Fatalf("charged failure %s priced %+v; want billed 105", a.Model, a)
				case a.Usage == nil && a.Billable:
					t.Fatalf("chargeless failure %s billed: %+v", a.Model, a)
				case a.Billable:
					billable++
				}
			}
			if billable != charged {
				t.Fatalf("%d charged failures billed; want all %d", billable, charged)
			}
		})
	}
}

// Control: a request whose record holds only failures that carry a charge
// (the gateway then sends nothing more) bills each of them exactly.
func TestFinalizeBillsAFullRecordOfChargedFailures(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(nil)
	rc.Response = nil
	for i := range models.MaxAttempts {
		addAttempt(rc, pastRecordAttempt(fmt.Sprintf("qwen/charged-%d", i), pastRecordFailedCard, models.OutcomeError, true))
	}
	p.ProcessResponse(context.Background(), rc)
	if b := rc.Billing; b.BilledMicros != models.MaxAttempts*105 || b.UpstreamMicros != models.MaxAttempts*100 {
		t.Fatalf("billed %d on %d upstream; want %d on %d, every charged failure", b.BilledMicros, b.UpstreamMicros,
			models.MaxAttempts*105, models.MaxAttempts*100)
	}
}
