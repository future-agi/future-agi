package spendguard

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// fixedEstimate holds every request at micros.
func fixedEstimate(micros int64) func(*models.RequestContext) int64 {
	return func(*models.RequestContext) int64 { return micros }
}

// refuser is a pre-plugin that refuses every request at its priority.
type refuser struct{ priority int }

func refuseAt(priority int) pipeline.Plugin { return refuser{priority} }

func (r refuser) Name() string  { return "refuser" }
func (r refuser) Priority() int { return r.priority }
func (r refuser) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultError(models.ErrPaymentRequired(models.ErrTypePaymentRequired, "managed_credits_exhausted", "no credits",
		models.PaymentMeta{LimitSource: models.LimitSourceCredits, Scope: models.ScopeOrg}))
}
func (r refuser) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func newEngine(plugins ...pipeline.Plugin) *pipeline.Engine { return pipeline.NewEngine(plugins...) }

// The spend guard counts a request at exactly what the credits plugin holds
// it at (the same worst-case estimate, G6's billing functions), without
// either side effect of the credits reserve.
func TestSpendGuardEstimateMatchesCreditsHold(t *testing.T) {
	cards := map[string]models.PriceCard{
		"openrouter/vendor/model": {Prompt: 2e-6, Completion: 10e-6},
		"openrouter/vendor/fee":   {Prompt: 1e-6, Completion: 3e-6, Request: 0.002},
	}
	prices := func(providerID, model, _ string) (models.PriceCard, bool) {
		c, ok := cards[providerID+"/"+model]
		return c, ok
	}
	maxOut := func(_, model string) int {
		if model == "vendor/fee" {
			return 900
		}
		return 0
	}
	orgMax := func(org string) int {
		if org == "org-small" {
			return 300
		}
		return 0
	}
	settings := creditsplugin.Settings{MarkupPercent: 5, ReserveDefaultOutputTokens: 4096, UnpricedReserveUSD: 0.10}

	est := &Estimator{Prices: prices, MaxOutput: maxOut, OrgMaxTokens: orgMax, Settings: settings}
	ledger := creditsplugin.NewOrgLedger(billing.NewMemoryLedger())
	credits := creditsplugin.New(true, auth.NewKeyStore(config.AuthConfig{}))
	credits.SetOrgLedger(ledger, true)
	credits.SetSettings(settings)
	credits.SetPrices(prices)
	credits.SetMaxOutput(maxOut)
	credits.SetOrgMaxTokens(orgMax)

	cases := map[string]func(rc *models.RequestContext){
		"max_tokens": func(rc *models.RequestContext) {},
		"no max_tokens, model max": func(rc *models.RequestContext) {
			rc.Request.MaxTokens = nil
			rc.Model, rc.Request.Model = "vendor/fee", "vendor/fee"
		},
		"no max_tokens, org max":    func(rc *models.RequestContext) { rc.Request.MaxTokens = nil; rc.Metadata["org_id"] = "org-small" },
		"no max_tokens, config max": func(rc *models.RequestContext) { rc.Request.MaxTokens = nil },
		"endpoint set": func(rc *models.RequestContext) {
			rc.Billing.EndpointSet = []models.EndpointRef{
				{ID: "a", Price: models.PriceCard{Prompt: 1e-6, Completion: 1e-6}},
				{ID: "b", Price: models.PriceCard{Prompt: 3e-6, Completion: 30e-6}},
				{ID: "c", Price: models.PriceCard{Prompt: 2e-6, Completion: 2e-6}},
			}
		},
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			org := "org-e"
			guarded := managedRC(org, "key_a", "g-"+name, 380)
			setup(guarded)
			held := managedRC(org, "key_a", "c-"+name, 380)
			setup(held)
			if _, err := ledger.Grant(bg, held.Metadata["org_id"], billing.GrantPush{GrantedTotalMicros: 1 << 40, Version: 1}); err != nil {
				t.Fatal(err)
			}

			got := est.Estimate(guarded)
			if res := credits.ProcessRequest(bg, held); res.Error != nil {
				t.Fatalf("credits reserve: %v", res.Error)
			}
			if got <= 0 || got != held.Billing.HoldMicros {
				t.Fatalf("spend guard estimate %d, credits hold %d", got, held.Billing.HoldMicros)
			}
			if guarded.Billing.ReservePrice != nil || guarded.Metadata["billing_alert"] != "" {
				t.Fatalf("the estimate wrote the request: reserve price %v, billing_alert %q",
					guarded.Billing.ReservePrice, guarded.Metadata["billing_alert"])
			}
			guarded.Release()
			held.Release()
		})
	}
}

// An unpriced model is the one request the two do not count alike. One
// Future AGI key serves only a model the catalogue prices, so the credits
// plugin refuses it with a 403 and holds nothing. The spend guard admits
// before the credits reserve and counts it at managed.unpriced_reserve_usd,
// more than the credits plugin holds, never less; the request then ends
// unbilled and the guard voids it.
func TestSpendGuardCountsAnUnpricedModelTheCreditsPluginRefuses(t *testing.T) {
	prices := func(providerID, model, _ string) (models.PriceCard, bool) {
		if providerID+"/"+model == "openrouter/vendor/model" {
			return models.PriceCard{Prompt: 2e-6, Completion: 10e-6}, true
		}
		return models.PriceCard{}, false
	}
	settings := creditsplugin.Settings{MarkupPercent: 5, ReserveDefaultOutputTokens: 4096, UnpricedReserveUSD: 0.10}
	est := &Estimator{Prices: prices, Settings: settings}
	ledger := creditsplugin.NewOrgLedger(billing.NewMemoryLedger())
	credits := creditsplugin.New(true, auth.NewKeyStore(config.AuthConfig{}))
	credits.SetOrgLedger(ledger, true)
	credits.SetSettings(settings)
	credits.SetPrices(prices)
	if _, err := ledger.Grant(bg, "org-u", billing.GrantPush{GrantedTotalMicros: 1 << 40, Version: 1}); err != nil {
		t.Fatal(err)
	}
	unpriced := func(requestID string) *models.RequestContext {
		rc := managedRC("org-u", "key_a", requestID, 380)
		rc.Model, rc.Request.Model = "vendor/unknown", "vendor/unknown"
		return rc
	}
	guarded, held := unpriced("g-unpriced"), unpriced("c-unpriced")
	defer guarded.Release()
	defer held.Release()

	got := est.Estimate(guarded)
	res := credits.ProcessRequest(bg, held)

	if res.Error == nil || res.Error.Status != http.StatusForbidden || res.Error.Code != "permission_denied" ||
		res.Error.Message != "model pricing is not available for Future AGI credits" {
		t.Fatalf("credits reserve of an unpriced model: %+v, want the 403 pricing refusal", res.Error)
	}
	if held.Billing.HoldMicros != 0 || held.Metadata["billing_alert"] != "unpriced" {
		t.Fatalf("refused request: hold %d micros, billing_alert %q; want no hold and unpriced",
			held.Billing.HoldMicros, held.Metadata["billing_alert"])
	}
	if want := billing.USDToMicros(settings.UnpricedReserveUSD); got != want {
		t.Fatalf("spend guard estimate %d, want the unpriced reserve %d", got, want)
	}
	if guarded.Billing.ReservePrice != nil || guarded.Metadata["billing_alert"] != "" {
		t.Fatalf("the estimate wrote the request: reserve price %v, billing_alert %q",
			guarded.Billing.ReservePrice, guarded.Metadata["billing_alert"])
	}
}
