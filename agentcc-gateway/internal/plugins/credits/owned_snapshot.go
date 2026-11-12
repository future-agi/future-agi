package credits

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// journalOf freezes the previous hops before reserving or dispatching the next.
// Pricing uses each attempt's admitted card and response, never the next hop's
// mutable BillingState. Missing evidence remains an unresolved full-hold debt.
func (p *Plugin) journalOf(rc *models.RequestContext) billing.Settlement {
	rec := settlementOf(rc, orgIDOf(rc))
	rec.ManagedJournal = map[string]billing.OwnedAttempt{}
	rec.BilledMicros, rec.UpstreamMicros = 0, 0
	attempts := rc.AttemptsSnapshot()
	rec.Attempts = attemptSummaries(attempts)
	for i, a := range attempts {
		rec.Attempts[i].AttemptID = a.FinancialID
	}
	if rec.Dimensional != nil {
		rec.Dimensional.InclusiveAttempts = nil
	}
	for i, a := range attempts {
		if a.CredentialType != models.CredentialManaged || a.Outcome == models.OutcomeSkipped || a.Outcome == models.OutcomeCircuitOpen {
			continue
		}
		hold := int64(0)
		var card models.PriceCard
		if a.Pricing != nil {
			hold = a.Pricing.HoldMicros
			if a.Pricing.ReservePrice != nil {
				card = *a.Pricing.ReservePrice
			}
		}
		j := billing.OwnedAttempt{Model: a.Model, Micros: hold, Dispatched: true, TokenPriced: card.Inclusive == nil}
		summary := billing.AttemptSummary{AttemptID: a.FinancialID, Model: a.Model, Provider: a.Provider, CredentialType: "managed", Kind: string(a.Kind), Outcome: string(a.Outcome)}
		if card.Inclusive != nil {
			j.Micros = a.Pricing.InclusiveHoldMicros
			binding := a.InclusiveIdentity
			j.Binding = &binding
		}
		resolved, evidence := cost.OwnedLiability(&a, a.Usage, card, p.settings.markupBps())
		if resolved.BilledMicros > 0 || resolved.UpstreamMicros > 0 {
			// An owned unknown outcome can owe its full hold without raw usage.
			// Preserve the base log's charge-bearing-slot protection before the
			// next AddAttempt, rather than waiting for final cost-plugin pricing.
			rc.UpdateAttempt(a.AttemptID, func(record *models.Attempt) { record.Billable = true })
		}
		summary.UpstreamMicros, summary.BilledMicros = resolved.UpstreamMicros, resolved.BilledMicros
		j.Summary, j.Evidence = &summary, evidence
		if j.TokenPriced && j.Summary != nil && j.Summary.BilledMicros > j.Micros {
			j.Anomaly = "provider charge exceeds admitted hold"
		}
		rec.ManagedJournal[a.FinancialID] = j
		rec.BilledMicros += resolved.BilledMicros
		rec.UpstreamMicros += resolved.UpstreamMicros
		rec.UsageEstimated = rec.UsageEstimated || resolved.Estimated
		rec.Attempts[i] = summary
		if evidence != nil && rec.Dimensional != nil {
			rec.Dimensional.InclusiveAttempts = append(rec.Dimensional.InclusiveAttempts, *evidence)
		}
	}
	rec.MarkupMicros = rec.BilledMicros - rec.UpstreamMicros
	return rec
}

// SealOwned is also called before BYOK bridges. They spend no managed credit,
// but cannot postpone a known managed anomaly until the request's final exit.
func (p *Plugin) SealOwned(rc *models.RequestContext) error {
	if !rc.Billing.OwnedRequest || rc.AttemptCount() == 0 {
		return nil
	}
	if p.orgLedger == nil {
		return billing.ErrUnavailable
	}
	owner, ok := p.orgLedger.Ledger().(interface {
		SealOwnedJournal(context.Context, string, string, billing.Settlement) error
	})
	if !ok {
		return billing.ErrUnavailable
	}
	return owner.SealOwnedJournal(context.Background(), orgIDOf(rc), rc.RequestID, p.journalOf(rc))
}
