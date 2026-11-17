package credits

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// BeginInclusive transfers hold ownership durably before upstream I/O. An old
// ledger or missing hold is a dispatch refusal, not an expiring best effort.
func (p *Plugin) BeginInclusive(rc *models.RequestContext, attemptID string) error {
	if !rc.Billing.Reserved || p.orgLedger == nil {
		return billing.ErrUnavailable
	}
	owner, ok := p.orgLedger.Ledger().(inclusive.OwnedLedger)
	if !ok {
		return billing.ErrUnavailable
	}
	var err error
	if durable, ok := owner.(interface {
		BeginInclusiveWithRecord(context.Context, string, string, string, billing.Settlement) error
	}); ok {
		rec := p.journalOf(rc)
		if card := rc.Billing.ReservePrice; card != nil && card.Inclusive != nil {
			rec.InclusiveBindings[attemptID] = rc.Billing.InclusiveIdentity
		}
		err = durable.BeginInclusiveWithRecord(context.Background(), orgIDOf(rc), rc.RequestID, attemptID, rec)
	} else {
		err = owner.BeginInclusive(context.Background(), orgIDOf(rc), rc.RequestID, attemptID)
	}
	if err != nil {
		return err
	}
	rc.Billing.InclusiveDispatched = true
	return nil
}

// A panic/timeout cannot send a dispatched inclusive hold down the void path.
// Estimate the full outstanding request hold once, retaining every available
// generation identity. A durable owner retries settlement after store failures.
func (p *Plugin) settleInclusiveOnExit(rc *models.RequestContext, org string) {
	p.settleOwned(rc, org, true)
}
