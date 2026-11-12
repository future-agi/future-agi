package budget

import (
	"encoding/json"
	"math"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The quote passed to both daily-cap buckets must match managed
// billing's configured markup. A request-only price card removes token-estimate
// uncertainty. These tests inspect the exact quote consumed by reserveCaps;
// they do not need Redis or claim to exercise the Redis settlement protocol.
func TestWalkMarkupDailyCapQuoteMatchesConfiguredCharge(t *testing.T) {
	for _, scope := range []string{"agent", "key"} {
		for _, source := range []string{"endpoint-set", "price-lookup"} {
			for _, mode := range []string{models.BillingModeManaged, models.BillingModeBYOK} {
				for _, tc := range []struct {
					name       string
					percent    float64
					useDefault bool
					wantBilled int64
					limitUSD   float64
				}{
					{name: "default", useDefault: true, wantBilled: 1_050_000, limitUSD: 1.05},
					{name: "zero", percent: 0, wantBilled: 1_000_000, limitUSD: 1.02},
					{name: "five", percent: 5, wantBilled: 1_050_000, limitUSD: 1.05},
					{name: "twenty", percent: 20, wantBilled: 1_200_000, limitUSD: 1.10},
					{name: "fractional", percent: 12.345, wantBilled: 1_123_500, limitUSD: 1.10},
				} {
					t.Run(scope+"/"+source+"/"+mode+"/"+tc.name, func(t *testing.T) {
						cfg := config.DefaultConfig()
						if !tc.useDefault {
							cfg.Managed.MarkupPercent = tc.percent
						}
						card := models.PriceCard{Request: 1, Source: models.PriceSourceCatalog}
						if !card.Billable() {
							t.Fatal("request-only price card must be billable")
						}
						rc := models.AcquireRequestContext()
						t.Cleanup(rc.Release)
						rc.Provider, rc.Model, rc.EndpointType = "synthetic", "text", "chat"
						rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "markup-org", "markup-key"
						rc.Metadata["billing_mode"] = mode
						rc.Request = &models.ChatCompletionRequest{Model: "text", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
						policy := &tenant.OrgConfig{
							Meta: &tenant.ConfigMeta{Version: 1},
							Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true,
								PolicyRevision: 1, NoMaxTokens: "clamp"},
						}
						cap := &tenant.AdmissionCapConfig{Limit: &tc.limitUSD, Period: "daily", ActivatedAt: "2026-10-27T00:00:00Z"}
						if scope == "agent" {
							policy.Budgets.Agents = map[string]*tenant.AdmissionCapConfig{"worker": cap}
							policy.Routing = &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{"markup-key": "worker"}}}
						} else {
							policy.Budgets.KeyIDs = map[string]*tenant.AdmissionCapConfig{"markup-key": cap}
						}
						a, refusal := ResolveAdmission(auth.Identity{OrgID: "markup-org", KeyID: "markup-key"}, policy, time.Now().Add(time.Minute))
						if refusal != nil || a == nil {
							t.Fatalf("resolve admission: admission=%v refusal=%v", a, refusal)
						}
						BindAdmission(rc, a)
						var prices models.PriceLookup
						if source == "endpoint-set" {
							rc.Billing.EndpointSet = []models.EndpointRef{{Price: card}}
						} else {
							prices = func(_, _, _ string) (models.PriceCard, bool) { return card, true }
						}
						if refusal := QualifyCapChat(rc, prices, int(math.Round(cfg.Managed.MarkupPercent*100)), cfg.Managed.ReserveDefaultOutputTokens); refusal != nil {
							t.Fatal(refusal)
						}
						credential := models.CredentialManaged
						want := tc.wantBilled
						if mode == models.BillingModeBYOK {
							credential, want = models.CredentialBYOK, 1_000_000
						}
						usage := models.MeasuredUsage(1, 0)
						upstream, billed, _, _, priced := cost.PriceAttempt(&models.Attempt{CredentialType: credential}, usage, card, int(math.Round(cfg.Managed.MarkupPercent*100)), 0)
						if !priced || upstream != 1_000_000 || billed != want {
							t.Fatalf("billing oracle: priced=%v upstream=%d billed=%d want=%d", priced, upstream, billed, want)
						}
						if !a.quoteReady || a.quoteMicros != billed {
							t.Errorf("daily reservation at %g%% markup: ready=%v reserved=%d billed=%d", cfg.Managed.MarkupPercent, a.quoteReady, a.quoteMicros, billed)
						}
						limit := a.keyLimit
						if scope == "agent" {
							limit = a.agentLimit
						}
						if quoteFits, chargeFits := a.quoteMicros <= limit, billed <= limit; quoteFits != chargeFits {
							t.Errorf("%s daily cap=%d: quoted admission=%v but billed charge fits=%v (reserved=%d billed=%d)", scope, limit, quoteFits, chargeFits, a.quoteMicros, billed)
						}
					})
				}
			}
		}
	}
}
