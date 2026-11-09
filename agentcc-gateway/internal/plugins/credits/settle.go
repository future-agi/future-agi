package credits

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// metaCreditsRemaining is the org's managed-credit balance after the
// request, in USD with six decimals (x-agentcc-managed-credits-remaining).
const metaCreditsRemaining = "managed_credits_remaining"

// finishManaged ends a managed request's billing after G7 priced it: SETTLE
// at rc.Billing.BilledMicros, or VOID when nothing was billed (an error, a
// cache hit, a short-circuit). It reports whether the request was billed to
// its org, so a managed key is never charged for it too.
func (p *Plugin) finishManaged(rc *models.RequestContext) bool {
	org := orgIDOf(rc)
	if !rc.Billing.Reserved {
		if !isManaged(rc) {
			return false
		}
		// Admitted without a hold (managed.require_credits off): the spend
		// is still settled.
		if !p.requireOrgCredits && rc.Billing.BilledMicros > 0 && !rc.Flags.CacheHit {
			p.settle(rc, org)
		}
		return true
	}
	switch {
	case rc.Flags.CacheHit:
		p.void(rc, org, "cache_hit")
	case len(rc.Billing.InclusiveOutcomes) > 0:
		p.settle(rc, org) // explicit provider cost zero is a spend, not a void
	case rc.Billing.BilledMicros <= 0:
		p.void(rc, org, "not_billed")
	default:
		p.settle(rc, org)
	}
	return true
}

// settle records the request's billed amount on its org and releases its
// hold, once (the settled marker makes a second SETTLE a no-op). A failed
// SETTLE is retried once. A dispatched inclusive hold remains durably owned by
// recovery; legacy holds retain their existing expiry behavior. Neither path is
// voided after an ambiguous acknowledgement.
func (p *Plugin) settle(rc *models.RequestContext, org string) {
	rec := settlementOf(rc, org)
	if rc.Billing.OwnedRequest {
		p.settleOwned(rc, org, false)
		return
	}
	p.settleRecord(rc, org, rec)
}

func (p *Plugin) settleRecord(rc *models.RequestContext, org string, rec billing.Settlement) {
	res, err := p.orgLedger.Settle(context.Background(), org, rc.RequestID, rec)
	if err != nil {
		res, err = p.orgLedger.Settle(context.Background(), org, rc.RequestID, rec)
	}
	rc.Billing.Reserved = false
	if err != nil {
		slog.Error("managed credits: settlement acknowledgement failed; inclusive liability remains recovery-owned",
			"request_id", rc.RequestID, "org_id", org, "billed_micros", rec.BilledMicros, "error", err)
		return
	}
	if res.Applied && !res.AccountMissing {
		p.recordRemaining(rc, res.Account)
	}
}

// void releases the request's hold and writes a void record, once.
func (p *Plugin) void(rc *models.RequestContext, org, reason string) {
	if rc.Billing.InclusiveDispatched {
		p.settleInclusiveOnExit(rc, org)
		return
	}
	rc.Billing.Reserved = false
	res, err := p.orgLedger.Void(context.Background(), org, rc.RequestID, settlementOf(rc, org))
	if err != nil {
		slog.Error("managed credits: void failed; the hold expires on its own", "request_id", rc.RequestID,
			"org_id", org, "reason", reason, "error", err)
		return
	}
	if res.Applied && !res.AccountMissing {
		p.recordRemaining(rc, res.Account)
	}
}

// voidOnExit is the request finalizer: it voids a hold that nothing settled
// or voided (a panic, a timeout, an exit path without post-plugins). It runs
// on its own context, as the request's may be done.
func (p *Plugin) voidOnExit(rc *models.RequestContext, org string) {
	if rc.Billing.Reserved {
		if rc.Billing.InclusiveDispatched {
			p.settleInclusiveOnExit(rc, org)
			return
		}
		p.void(rc, org, "finalizer")
	}
}

// recordRemaining puts the org's balance after the request on rc.
func (p *Plugin) recordRemaining(rc *models.RequestContext, acct billing.Account) {
	available := acct.AvailableMicros()
	rc.Billing.CreditsRemainingMicros = &available
	rc.Metadata[metaCreditsRemaining] = fmt.Sprintf("%.6f", billing.MicrosToUSD(available))
}
