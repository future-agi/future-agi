package credits

import (
	"context"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// metaBillingAlert is set to "unpriced" on a managed request held at
// managed.unpriced_reserve_usd because its model has no price.
const metaBillingAlert = "billing_alert"

// reserve holds a managed request at its worst-case cost (RESERVE mode new,
// keyed by the request id) and registers the finalizer that voids the hold
// if nothing settles it. It returns the refusal to send instead, or nil.
func (p *Plugin) reserve(rc *models.RequestContext) *models.APIError {
	if !p.requireOrgCredits {
		return nil
	}
	refused, err := p.hold(rc, p.primaryEstimate(rc), billing.ReserveNew)
	if err != nil {
		return ErrBillingUnavailable()
	}
	return refused
}

// ReserveAdd takes a managed fallback hop's own hold (RESERVE mode add, on
// the request's hold) at the hop model's worst-case cost, and reports whether
// the hop may run (server.BillingHooks). A hop billed to the caller's own
// credentials holds nothing. A refused hold is never retried: the hop is
// skipped (routing.ErrHopSkipped).
func (p *Plugin) ReserveAdd(rc *models.RequestContext, providerID, model string) (bool, error) {
	if !p.enabled || p.orgLedger == nil || !p.requireOrgCredits || !isManaged(rc) {
		return true, nil
	}
	// The hop is another model: the primary's endpoint does not price it.
	micros, _ := p.modelEstimate(rc, providerID, model, "")
	refused, err := p.hold(rc, micros, billing.ReserveAdd)
	if err != nil {
		return false, err
	}
	return refused == nil, nil
}

// Void releases the request's unsettled hold (server.BillingHooks).
func (p *Plugin) Void(rc *models.RequestContext, reason string) {
	if p.orgLedger != nil && rc.Billing.Reserved {
		p.void(rc, orgIDOf(rc), reason)
	}
}

// hold runs RESERVE for micros on rc's hold. On ok it records the hold on
// rc.Billing and, for the request's first hold, registers the finalizer, and
// returns nil, nil. A hold that does not fit returns its 402; a missing
// account returns the 503 account_state_missing and asks the drain to
// re-push the account. err is the store's failure: managed billing fails
// closed on it.
func (p *Plugin) hold(rc *models.RequestContext, micros int64, mode billing.ReserveMode) (refused *models.APIError, err error) {
	org := orgIDOf(rc)
	res, err := p.orgLedger.Reserve(context.Background(), org, rc.RequestID, micros, p.settings.holdTTL(), mode)
	if err != nil {
		slog.Error("managed credits: reserve failed; refusing the managed request", "request_id", rc.RequestID, "org_id", org, "error", err)
		return nil, err
	}
	switch res.Status {
	case billing.ReserveOK:
		p.held(rc, org, micros, res.Account)
		return nil, nil
	case billing.ReserveNoAccount:
		if err := p.orgLedger.MarkNeedsPush(context.Background(), org); err != nil {
			slog.Error("managed credits: could not ask for the account's re-push", "org_id", org, "error", err)
		}
		return errAccountMissing(p.settings.retryAfterSec()), nil
	default:
		return refusal(res.Status, micros, res.Account), nil
	}
}

// held records a hold on rc. The request's first hold registers the
// finalizer that voids it on any exit path nothing settled it on.
func (p *Plugin) held(rc *models.RequestContext, org string, micros int64, acct billing.Account) {
	first := !rc.Billing.Reserved
	rc.Billing.Reserved = true
	rc.Billing.HoldMicros += micros
	available := acct.AvailableMicros()
	rc.Billing.CreditsRemainingMicros = &available
	if first {
		rc.AddFinalizer(func() { p.voidOnExit(rc, org) })
	}
}

// primaryEstimate is the request's hold: the highest card in its endpoint
// set when routing filled one (stored as rc.Billing.ReservePrice), else its
// model's card on its provider.
func (p *Plugin) primaryEstimate(rc *models.RequestContext) int64 {
	if set := rc.Billing.EndpointSet; len(set) > 0 {
		card, micros, _ := billing.HighestCard(set, rc.Request, p.settings.markupBps(), p.defaultMaxOut(rc, rc.Provider, modelOf(rc)))
		rc.Billing.ReservePrice = &card
		return micros
	}
	micros, _ := p.modelEstimate(rc, rc.Provider, modelOf(rc), rc.Billing.EndpointID)
	return micros
}

// modelEstimate is model's worst-case cost on providerID through endpointID.
// An unpriced model is held at managed.unpriced_reserve_usd and marked with
// billing_alert=unpriced.
func (p *Plugin) modelEstimate(rc *models.RequestContext, providerID, model, endpointID string) (int64, bool) {
	var card models.PriceCard
	priced := false
	if p.prices != nil {
		card, priced = p.prices(providerID, model, endpointID)
	}
	if !priced {
		rc.Metadata[metaBillingAlert] = "unpriced"
		return billing.USDToMicros(p.settings.UnpricedReserveUSD), false
	}
	return billing.EstimateWorstCaseMicros(rc.Request, card, p.settings.markupBps(), p.defaultMaxOut(rc, providerID, model)), true
}

// defaultMaxOut is the output a request with no max_tokens is held for:
// min(the model's max output, the org's default_max_tokens_estimate or
// managed.reserve_default_output_tokens).
func (p *Plugin) defaultMaxOut(rc *models.RequestContext, providerID, model string) int {
	configured := p.settings.ReserveDefaultOutputTokens
	if p.orgMaxTokens != nil {
		if n := p.orgMaxTokens(orgIDOf(rc)); n > 0 {
			configured = n
		}
	}
	modelMax := 0
	if p.maxOutput != nil {
		modelMax = p.maxOutput(providerID, model)
	}
	return billing.DefaultMaxOut(modelMax, configured)
}

// modelOf is the model the request is sent upstream as.
func modelOf(rc *models.RequestContext) string {
	if rc.Request != nil && rc.Request.Model != "" {
		return rc.Request.Model
	}
	return rc.Model
}
