package cost

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestNativeHoldFloor(t *testing.T) {
	prompt := 1000
	partial := models.NewUsage(&prompt, nil)
	zeroCharge := 0.0
	charge := &models.Usage{Cost: &zeroCharge}
	for _, tc := range []struct {
		name                                string
		native, managed, serving, estimated bool
		usage                               *models.Usage
		billed, want                        int64
	}{
		{"incomplete", true, true, true, false, partial, 2100, 43025},
		{"absent", true, true, true, false, nil, 0, 43025},
		{"estimated", true, true, true, true, models.MeasuredUsage(8, 3), 48, 43025},
		{"evidence-above-hold", true, true, true, true, partial, 60000, 60000},
		{"explicit-zero", true, true, true, false, models.MeasuredUsage(0, 0), 0, 0},
		{"reported-zero-cost", true, true, true, true, charge, 0, 0},
		{"complete", true, true, true, false, models.MeasuredUsage(10, 5), 74, 74},
		{"byok", true, false, true, true, partial, 2100, 2100},
		{"openai-azure", false, true, true, true, partial, 2100, 2100},
		{"failed-before-output", true, true, false, false, partial, 2100, 2100},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := &Plugin{markupBps: 500}
			a := &models.Attempt{NativeTokenUsage: tc.native, Pricing: &models.AttemptPricing{HoldMicros: 43025}}
			c := attemptCost{billable: true, managed: tc.managed, estimated: tc.estimated, billed: tc.billed, upstream: 2000,
				upstreamFeeBps: 100, processingFeeBps: 200, source: models.CostSourcePriceCard}
			p.nativeHoldFloor(a, tc.usage, tc.serving, false, &c)
			if c.billed != tc.want {
				t.Fatalf("billed=%d want=%d", c.billed, tc.want)
			}
			if c.billed > tc.billed {
				if !c.estimated || !c.priced || c.upstream != 40976 || c.landed != scaleBps(c.upstream, 10100)+scaleBps(c.billed, 200) {
					t.Fatalf("inconsistent monetary estimate: %+v", c)
				}
			}
			before := c
			p.nativeHoldFloor(a, tc.usage, tc.serving, false, &c)
			if c != before {
				t.Fatal("floor changed on replay")
			}
		})
	}
}

func TestNativeHoldSnapshot(t *testing.T) {
	b := models.BillingState{HoldMicros: 80000, AttemptHoldMicros: 30000}
	a := models.NewAttemptPricing(&b)
	b.HoldMicros += 60000
	b.AttemptHoldMicros = 60000
	if a.HoldMicros != 30000 || models.NewAttemptPricing(&b).HoldMicros != 60000 {
		t.Fatal("attempt estimate inherited cumulative hold or later hop")
	}
}

// An embedding has no output dimension: its measured input is the whole
// usage. A credit-funded native embedding with a positive admission hold (the
// managed private-search embedding) must settle exactly that measured input,
// not the hold floor reserved for incomplete native usage.
func TestNativeHoldManagedEmbeddingSettlesExactly(t *testing.T) {
	for _, tc := range []struct {
		name      string
		estimated bool
		want      int64
	}{
		{"measured", false, 2100},
		// An embedding the adapter had to estimate is still floored.
		{"estimated", true, 43025},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := New(true, func() *modeldb.ModelDB { return modeldb.New(nil, nil) }, nil, nil)
			p.SetManagedMarkup(5)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID, rc.Model, rc.Provider, rc.EndpointType = "req-embed", "acme-embed", "native-direct", "embedding"
			rc.Metadata["billing_mode"] = models.BillingModeManaged
			if tc.estimated {
				rc.Metadata["usage_estimated"] = "true"
			}
			rc.EmbeddingResponse = &models.EmbeddingResponse{Object: "list", Model: "acme-embed",
				Usage: &models.EmbeddingUsage{PromptTokens: 1000, TotalTokens: 1000}}
			rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "native-direct", Model: "acme-embed",
				CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess, NativeTokenUsage: true,
				Pricing: &models.AttemptPricing{HoldMicros: 43025, ReservePrice: &models.PriceCard{Prompt: 0.000002, Source: models.PriceSourcePriceCard}}})

			p.FinalizeRequest(rc)

			attempts := rc.AttemptsSnapshot()
			if len(attempts) != 1 || rc.Billing.BilledMicros != tc.want || attempts[0].BilledCostMicros != tc.want {
				t.Fatalf("billed request=%d attempts=%+v, want %d", rc.Billing.BilledMicros, attempts, tc.want)
			}
			if rc.Billing.UsageEstimated != tc.estimated || attempts[0].UsageEstimated != tc.estimated {
				t.Errorf("estimate provenance request=%t attempt=%t, want %t", rc.Billing.UsageEstimated, attempts[0].UsageEstimated, tc.estimated)
			}
			if attempts[0].PromptTokens != 1000 || attempts[0].CompletionTokens != 0 {
				t.Errorf("attempt tokens = %d/%d, want 1000/0", attempts[0].PromptTokens, attempts[0].CompletionTokens)
			}
		})
	}
}
