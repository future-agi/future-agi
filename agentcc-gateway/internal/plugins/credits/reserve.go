package credits

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/admission"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
)

// metaBillingAlert identifies a managed request refused for missing or
// unsupported pricing, without a hold or upstream dispatch.
const metaBillingAlert = "billing_alert"

// reserve holds a managed request at its worst-case cost (RESERVE mode new,
// keyed by the request id) and registers the finalizer that voids the hold
// if nothing settles it. It returns the refusal to send instead, or nil.
func (p *Plugin) reserve(rc *models.RequestContext) *models.APIError {
	if !p.requireOrgCredits {
		return nil
	}
	micros, priced := p.primaryEstimate(rc)
	if !priced {
		return models.ErrForbidden("model pricing is not available for Future AGI credits")
	}
	refused, err := p.hold(rc, modelOf(rc), micros, billing.ReserveNew)
	if err != nil {
		return ErrBillingUnavailable()
	}
	return refused
}

// ReserveAdd takes a managed fallback hop's own hold (RESERVE mode add, on
// the request's hold) at the hop model's worst-case cost, and reports whether
// the hop may run (BillingHooks). A hop billed to the caller's own
// credentials holds nothing. A refused hold is never retried: the hop is
// skipped (the hop-skipped sentinel).
func (p *Plugin) ReserveAdd(rc *models.RequestContext, providerID, model string) (bool, error) {
	if err := p.SealOwned(rc); err != nil {
		return false, err
	}
	// Sealing prices unknown owned outcomes too; a full record of liabilities
	// must not acquire a fallback hold it cannot record or dispatch.
	if rc.Billing.OwnedRequest && !rc.CanRecordAttempt() {
		return false, nil
	}
	if !p.enabled || p.orgLedger == nil || !p.requireOrgCredits || !isManaged(rc) {
		return true, nil
	}
	// Routing replaces the admitted card and endpoint set for each hop.
	micros, priced := p.admittedEstimate(rc, providerID, model)
	if !priced {
		return false, nil
	}
	refused, err := p.hold(rc, model, micros, billing.ReserveAdd)
	if err != nil {
		return false, err
	}
	return refused == nil, nil
}

// Void releases the request's unsettled hold (BillingHooks).
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
func (p *Plugin) hold(rc *models.RequestContext, model string, micros int64, mode billing.ReserveMode) (refused *models.APIError, err error) {
	org := orgIDOf(rc)
	// A changed price-card/source cannot bypass a previously quarantined listing.
	// This check is also used for ordinary token-priced reservations. Inclusive
	// reservations additionally confirm their provisional hold at the global fence.
	if gate, ok := p.orgLedger.Ledger().(interface {
		InclusiveQuarantined(context.Context, string, string) (bool, error)
	}); ok {
		blocked, e := gate.InclusiveQuarantined(context.Background(), org, model)
		if e != nil {
			return nil, e
		}
		if blocked {
			return nil, billing.ErrOwnership
		}
	}
	var res billing.ReserveResult
	if card := rc.Billing.ReservePrice; rc.Billing.OwnedRequired || rc.Billing.OwnedRequest || (card != nil && card.Inclusive != nil) {
		owner, ok := p.orgLedger.Ledger().(inclusive.OwnedLedger)
		if !ok {
			return nil, billing.ErrUnavailable
		}
		tokenPriced := card == nil || card.Inclusive == nil
		// Allocate before the durable reserve. Failed/undispatched reservations
		// consume a sequence too: their identity must never be reused by a hop.
		attemptID := rc.NewFinancialAttemptID()
		rc.Billing.OwnedAttemptID = ""
		if mixed, ok := owner.(interface {
			ReserveOwned(context.Context, string, string, string, string, int64, time.Duration, billing.ReserveMode, bool, billing.Settlement) (billing.ReserveResult, error)
		}); ok {
			res, err = mixed.ReserveOwned(context.Background(), org, rc.RequestID, attemptID, model, micros, p.settings.holdTTL(), mode, tokenPriced, p.journalOf(rc))
		} else if !tokenPriced && rc.AttemptCount() == 0 {
			res, err = owner.ReserveInclusive(context.Background(), org, rc.RequestID, attemptID, model, micros, p.settings.holdTTL(), mode)
		} else {
			return nil, billing.ErrOwnership
		}
		if err == nil && res.Status == billing.ReserveOK {
			rc.Billing.OwnedRequest = true
			rc.Billing.OwnedAttemptID = attemptID
		}
	} else {
		res, err = p.orgLedger.Reserve(context.Background(), org, rc.RequestID, micros, p.settings.holdTTL(), mode)
	}
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
		return refusal(res.Status, micros, res.Account, p.settings.GrantOnlyCredits), nil
	}
}

// held records a hold on rc. The request's first hold registers the
// finalizer that voids it on any exit path nothing settled it on.
func (p *Plugin) held(rc *models.RequestContext, org string, micros int64, acct billing.Account) {
	first := !rc.Billing.Reserved
	rc.Billing.Reserved = true
	rc.Billing.HoldMicros += micros
	rc.Billing.AttemptHoldMicros = micros
	available := acct.AvailableMicros()
	rc.Billing.CreditsRemainingMicros = &available
	if first {
		rc.AddFinalizer(func() { p.voidOnExit(rc, org) })
	}
}

// primaryEstimate is the request's hold: the highest card in its endpoint
// set when routing filled one (stored as rc.Billing.ReservePrice), else its
// model's card on its provider.
func (p *Plugin) primaryEstimate(rc *models.RequestContext) (int64, bool) {
	return p.admittedEstimate(rc, rc.Provider, modelOf(rc))
}

func (p *Plugin) admittedEstimate(rc *models.RequestContext, providerID, model string) (int64, bool) {
	if capbudget.NativeCapsRequired(rc) {
		if providerID != rc.Provider || model != rc.Model || rc.AttemptCount() != 0 {
			return 0, false
		}
		q, ok := capbudget.NativeQuoteFor(rc)
		if !ok {
			return 0, false
		}
		// The sealed native quote already pins the exact price snapshot and
		// managed bound. Never reprice nil chat or take another money path.
		return q.ManagedHoldMicros()
	}
	// Capture caller extensions before the first upstream rewrite. Later copies
	// retain them, even if a token hop stripped/overwrote them. Only the gateway's
	// subsequent routing injections disappear; caller extensions still refuse.
	if !rc.Billing.AdmissionCaptured && rc.Request != nil {
		rc.Billing.AdmissionCaptured = true
		rc.Billing.AdmissionExtra = map[string]json.RawMessage{}
		for k, v := range rc.Request.Extra {
			rc.Billing.AdmissionExtra[k] = append(json.RawMessage(nil), v...)
		}
	}
	est := admission.WorstCase(rc, providerID, model, p.prices, p.settings.markupBps(), p.defaultMaxOut(rc, providerID, model))
	rc.Billing.InclusiveHoldMicros = 0
	if est.Inclusive {
		rc.Billing.InclusiveHoldMicros = est.Micros
	}
	if est.Card != nil {
		rc.Billing.ReservePrice = est.Card
	}
	if est.Unpriced {
		rc.Metadata[metaBillingAlert] = "unpriced"
	}
	return est.Micros, est.Priced
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
