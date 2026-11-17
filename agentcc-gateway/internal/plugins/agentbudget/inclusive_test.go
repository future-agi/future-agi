package agentbudget

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInclusiveEstimateUsesAdmittedCard(t *testing.T) {
	// Keep positive scalar fields: checking Completion alone must not permit
	// clamping an inclusive card, even when 256 scalar tokens fit under the cap.
	card := models.PriceCard{Completion: 1e-6, Inclusive: &models.InclusivePrice{Model: "m", Generation: "0.04"}}
	for _, route := range []string{"pinned", "endpoints", "lookup"} {
		t.Run(route, func(t *testing.T) {
			p := testPlugin(nil, cardTokens, nil)
			rc := call(t, 1024)
			rc.Metadata["billing_mode"] = models.BillingModeManaged
			switch route {
			case "pinned":
				rc.Billing.ReservePrice = &card
			case "endpoints":
				rc.Billing.EndpointSet = []models.EndpointRef{{Price: cardTokens}, {Price: card}}
			case "lookup":
				p = testPlugin(nil, card, nil)
			}
			want, err := inclusive.Bound(rc.Request, card.Inclusive)
			if err != nil {
				t.Fatal(err)
			}
			capUSD := float64(want-1) / 1e6
			rc.Agent.MaxCostUSD = &capUSD
			apiErr := admit(p, rc, nil)
			wantRefusal(t, apiErr, 402, CodeCallCapExceeded, models.LimitSourceCallCap)
			if *rc.Request.MaxTokens != 1024 || header(rc, HeaderMaxTokensClamped) != "" {
				t.Fatal("inclusive call was clamped")
			}
			est := p.estimate(rc, p.statesValue(rc))
			if !est.priced || est.micros != want || est.card.Inclusive == nil {
				t.Fatalf("estimate=%+v want=%d", est, want)
			}
			if _, _, ok := clampOutput(rc.Request, &est, want-1); ok {
				t.Fatal("inclusive card can be clamped")
			}
		})
	}
}

// Retain token arithmetic, both markup modes, endpoint selection, and the
// model/org default-output bound while using the shared admission estimator.
func TestSharedEstimateTokenControls(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, route := range []string{"lookup", "pinned", "endpoints"} {
			t.Run(route+map[bool]string{false: "/byok", true: "/managed"}[managed], func(t *testing.T) {
				out := 512
				cfg := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{DefaultMaxTokensEstimate: &out}}
				p := testPlugin(nil, cardTokens, cfg)
				rc := inRun(call(t, 0), "control", -1)
				bps := 0
				if managed {
					rc.Metadata["billing_mode"] = models.BillingModeManaged
					bps = 500
				}
				if route == "pinned" {
					rc.Billing.ReservePrice = &cardTokens
				}
				if route == "endpoints" {
					rc.Billing.EndpointSet = []models.EndpointRef{{Price: cardTokens}, {Price: cardFee10k}}
				}
				st := p.begin(rc, cfg, true)
				est := p.estimate(rc, st)
				want := billing.EstimateWorstCaseMicros(rc.Request, cardTokens, bps, out)
				if route == "endpoints" {
					_, want, _ = billing.HighestCard(rc.Billing.EndpointSet, rc.Request, bps, out)
				}
				if !est.priced || est.micros != want {
					t.Fatalf("estimate=%+v want=%d", est, want)
				}
			})
		}
	}
}

func TestAdmissionPrecedesCreditsButSettlementFollows(t *testing.T) {
	p := testPlugin(nil, cardTokens, nil)
	if p.Priority() != 509 || p.PostPriority() != 515 {
		t.Fatalf("run budget priority=%d post=%d, want 509/515 around credits 510", p.Priority(), p.PostPriority())
	}
}

func (p *Plugin) statesValue(rc *models.RequestContext) *runState {
	st, _ := p.states.Load(rc)
	return st.(*runState)
}
