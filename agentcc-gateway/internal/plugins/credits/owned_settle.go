package credits

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// settleOwned seals the last log delta, then reads complete financial evidence
// from its owner. Compaction is solely a logging concern: neither live settlement
// nor a panic/timeout finalizer may treat the retained 16 rows as the journal.
func (p *Plugin) settleOwned(rc *models.RequestContext, org string, recovery bool) {
	rec, err := p.ownedRecord(rc, org, recovery)
	if err != nil {
		// Fail closed; do not release/void an unknown liability or send a partial
		// settlement. The independent durable recovery worker owns this request.
		rc.Billing.Reserved = false
		slog.Error("managed credits: owned evidence unavailable; liability remains recovery-owned",
			"request_id", rc.RequestID, "org_id", org, "error", err)
		return
	}
	p.settleRecord(rc, org, rec)
}

func (p *Plugin) ownedRecord(rc *models.RequestContext, org string, recovery bool) (billing.Settlement, error) {
	if p.orgLedger == nil {
		return billing.Settlement{}, billing.ErrUnavailable
	}
	owner, ok := p.orgLedger.Ledger().(interface {
		SealOwnedJournal(context.Context, string, string, billing.Settlement) error
		ReadOwnedJournal(context.Context, string, string) (billing.OwnedRequest, error)
	})
	if !ok {
		return billing.Settlement{}, billing.ErrUnavailable
	}
	rec := p.journalOf(rc)
	if err := owner.SealOwnedJournal(context.Background(), org, rc.RequestID, rec); err != nil {
		return billing.Settlement{}, err
	}
	owned, err := owner.ReadOwnedJournal(context.Background(), org, rc.RequestID)
	if err != nil {
		return billing.Settlement{}, err
	}
	if recovery {
		return owned.RecoveryRecord(org, rc.RequestID, time.Now()), nil
	}
	return owned.CompleteRecord(rec)
}
