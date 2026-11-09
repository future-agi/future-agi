package spendguard

import (
	"math"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/admission"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// Estimator is a managed request's worst-case cost, computed exactly as the
// credits plugin computes the hold it takes right after the spend guard
// (admission.WorstCase, including the bounded inclusive card), without writing the
// request: the credits reserve records ReservePrice and billing_alert itself.
// TestSpendGuardEstimateMatchesCreditsHold keeps the two equal.
//
// An unpriced model is counted at managed.unpriced_reserve_usd. The credits
// plugin refuses that request with a 403 and holds nothing (one Future AGI
// key serves only a model the catalogue prices), so the guard counts it at
// more than its hold, never less, until the request ends unbilled and the
// guard voids it (TestSpendGuardCountsAnUnpricedModelTheCreditsPluginRefuses).
//
// Its fields are read per request: set them before the gateway serves.
type Estimator struct {
	// Prices is the gateway's injected price lookup; nil leaves every model
	// unpriced.
	Prices models.PriceLookup
	// MaxOutput is a model's max output tokens, 0 when unknown.
	MaxOutput func(providerID, model string) int
	// OrgMaxTokens is the org's managed_models.default_max_tokens_estimate, 0
	// when unset.
	OrgMaxTokens func(orgID string) int
	// Settings are the managed.* values the credits plugin holds with.
	Settings creditsplugin.Settings
}

// Estimate is rc's worst-case cost in micros, markup included.
func (e *Estimator) Estimate(rc *models.RequestContext) int64 {
	markup := markupBps(e.Settings.MarkupPercent)
	model := modelOf(rc)
	est := admission.WorstCase(rc, rc.Provider, model, e.Prices, markup, e.defaultMaxOut(rc, rc.Provider, model))
	if est.Unpriced {
		return billing.USDToMicros(e.Settings.UnpricedReserveUSD)
	}
	return est.Micros
}

// defaultMaxOut is the output a request with no max_tokens is held for:
// min(the model's max output, the org's default_max_tokens_estimate or
// managed.reserve_default_output_tokens).
func (e *Estimator) defaultMaxOut(rc *models.RequestContext, providerID, model string) int {
	configured := e.Settings.ReserveDefaultOutputTokens
	if e.OrgMaxTokens != nil {
		if n := e.OrgMaxTokens(orgIDOf(rc)); n > 0 {
			configured = n
		}
	}
	modelMax := 0
	if e.MaxOutput != nil {
		modelMax = e.MaxOutput(providerID, model)
	}
	return billing.DefaultMaxOut(modelMax, configured)
}

// markupBps is a markup percent in basis points: 5 is 500.
func markupBps(percent float64) int {
	if !(percent > 0) {
		return 0
	}
	return int(math.Round(percent * 100))
}

// modelOf is the model the request is sent upstream as.
func modelOf(rc *models.RequestContext) string {
	if rc.Request != nil && rc.Request.Model != "" {
		return rc.Request.Model
	}
	return rc.Model
}
