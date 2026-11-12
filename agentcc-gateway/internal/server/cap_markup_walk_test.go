package server

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Observe the real handler's quote, then stop before any ledger or provider
// work. Redis reservation/settlement is deliberately not part of this test.
type walkMarkupQuoteObserver struct {
	t         *testing.T
	mode      string
	markupBps int
	prices    models.PriceLookup
	seen      bool
}

func (*walkMarkupQuoteObserver) Name() string  { return "walk-markup-quote" }
func (*walkMarkupQuoteObserver) Priority() int { return -1000 }
func (p *walkMarkupQuoteObserver) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.seen = true
	if rc.Metadata["billing_mode"] != p.mode {
		p.t.Fatalf("billing mode=%q want=%q", rc.Metadata["billing_mode"], p.mode)
	}
	a := capbudget.RequestAdmission(rc)
	if a == nil {
		p.t.Fatal("handler did not bind daily-cap admission")
	}
	if err := capbudget.RecheckCapChat(rc); err != nil {
		p.t.Fatalf("handler did not prepare a stable quote: %v", err)
	}
	bps := p.markupBps
	if p.mode == models.BillingModeBYOK {
		bps = 0
	}
	set := rc.Billing.EndpointSet
	if len(set) == 0 {
		card, ok := p.prices(rc.Provider, rc.Request.Model, rc.Billing.EndpointID)
		if !ok {
			p.t.Fatal("fixture has no exact route price")
		}
		set = []models.EndpointRef{{Price: card}}
	}
	_, want, ok := billing.HighestCard(set, rc.Request, bps, 4096)
	if !ok || want <= 0 {
		p.t.Fatal("fixture must have a positive exact endpoint quote")
	}
	// QualifiedQuote exposes the amount even before the ledger qualifies it.
	got, qualified := a.QualifiedQuote()
	if qualified || got != want {
		p.t.Errorf("handler cap quote=%d qualified=%v want=%d at %d bps", got, qualified, want, bps)
	}
	return pipeline.ResultError(&models.APIError{Status: http.StatusTeapot, Type: models.ErrTypeInvalidRequest, Code: "walk_markup_observed", Message: "quote observed before reservation"})
}
func (*walkMarkupQuoteObserver) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func TestWalkMarkupHandlerUsesConfiguredDailyCapQuote(t *testing.T) {
	for _, mode := range []string{models.BillingModeManaged, models.BillingModeBYOK} {
		for _, tc := range []struct {
			name       string
			percent    float64
			bps        int
			useDefault bool
		}{
			{name: "default", bps: 500, useDefault: true},
			{name: "zero", percent: 0, bps: 0},
			{name: "five", percent: 5, bps: 500},
			{name: "twenty", percent: 20, bps: 2000},
			{name: "fractional", percent: 12.345, bps: 1235},
		} {
			t.Run(mode+"/"+tc.name, func(t *testing.T) {
				observer := &walkMarkupQuoteObserver{t: t, mode: mode, markupBps: tc.bps}
				g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{
					extraPlugins: []pipeline.Plugin{observer},
					configure: func(cfg *config.Config) {
						if !tc.useDefault {
							cfg.Managed.MarkupPercent = tc.percent
						}
					},
				})
				// Exercise the same cfg -> EnableManaged -> handler binding as startup.
				settings := g.srv.managed
				settings.MarkupPercent = g.srv.cfg.Managed.MarkupPercent
				g.srv.EnableManaged(settings)
				observer.prices = g.srv.handlers.Deps().Prices
				key := g.srv.keyStore.Authenticate(managedOrgKey)
				limit := 1.0
				cfg := managedOptIn()
				cfg.Meta = &tenant.ConfigMeta{Version: 1}
				cfg.Budgets = &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true,
					PolicyRevision: 1, NoMaxTokens: "clamp",
					KeyIDs: map[string]*tenant.AdmissionCapConfig{key.ID: {Limit: &limit, Period: "daily", ActivatedAt: "2026-10-27T00:00:00Z"}}}
				model := "openai/gpt-6-sol"
				if mode == models.BillingModeBYOK {
					cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
					model = "gpt-4o"
				}
				g.tenants.Set("org-m", cfg)
				g.grant(t, "org-m", 10_000_000)
				w := g.chat(t, chatBody(model), nil)
				if !observer.seen || w.Code != http.StatusTeapot || errorCode(t, w) != "walk_markup_observed" {
					t.Fatalf("quote boundary not reached: seen=%v status=%d body=%s", observer.seen, w.Code, w.Body)
				}
				if g.managed.calls() != 0 || g.operator.calls() != 0 {
					t.Fatal("quote wiring test dispatched upstream")
				}
			})
		}
	}
}
