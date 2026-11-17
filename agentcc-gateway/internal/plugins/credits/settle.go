package credits

import (
	"context"
	"fmt"
	"log/slog"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// metaCreditsRemaining is the org's managed-credit balance after the
// request, in USD with six decimals (x-agentcc-managed-credits-remaining).
const metaCreditsRemaining = "managed_credits_remaining"

// metaUsageUnreadable is the request metadata key that says why a request
// was priced on an estimate rather than the usage the provider reported. The
// gateway reserves it (server usageUnreadableMetadataKey), so caller
// metadata never sets it.
const metaUsageUnreadable = "usage_unreadable"

// UsageDispatchedWithoutUsage is metaUsageUnreadable's value on a managed
// request whose latest attempt was sent to the upstream and ended without
// usage: the caller left or a timeout cut the call, the connection was lost
// after the send, or a stream was cut before its usage chunk. The provider
// was asked and may have charged for it, so that attempt is charged at its
// hold (chargeAtHold), never voided. An earlier attempt left the same way
// carries models.Attempt.DispatchedWithoutUsage and is charged at its own
// hold too.
const UsageDispatchedWithoutUsage = "dispatched_without_usage"

// finishManaged ends a managed request's billing after the cost plugin priced it: SETTLE
// at rc.Billing.BilledMicros, or VOID when nothing was billed (an error, a
// cache hit, a short-circuit). An attempt dispatched without usage is charged
// at its hold (chargeAtHold). It reports whether the request was billed to
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
	case p.chargeAtHold(rc):
		p.settle(rc, org) // dispatched without usage: its hold, never a void
	case rc.Billing.BilledMicros <= 0:
		p.void(rc, org, "not_billed")
	default:
		p.settle(rc, org)
	}
	return true
}

// chargeAtHold charges every managed attempt the gateway sent that ended
// without an answer or usage at the hold that attempt reserved, on top of
// what the request's other attempts billed, and reports whether it charged
// any. Such an attempt is one the gateway flagged
// (models.Attempt.DispatchedWithoutUsage), or the request's latest attempt
// when the gateway marked the request (UsageDispatchedWithoutUsage). Owned
// and inclusive requests price their unknown outcomes themselves (cost
// priceOwned, billing.RecoveryRecord). An attempt that is not managed, that
// the provider rejected, or whose usage the upstream reported keeps its
// price.
//
// Each such attempt's priced amount (nothing for a failed call, the
// visible-text estimate of a cut stream) is replaced by exactly its hold, the
// reservation snapshotted on it (models.NewAttemptPricing), never a new
// quote; the request's other attempts keep theirs, so a fallback that served
// still bills its usage. A fallback hop takes its own hold (ReserveAdd), and
// so does a retry of a flagged attempt (server retryConsumesHold), so the sum
// stays within the request's hold. The hold's upstream part removes the
// markup the hold was reserved with (admittedEstimate's
// p.settings.markupBps(); the attempt's pricing snapshot does not carry it),
// the relation billing.RecoveryRecord applies at 5% (missing*100/105).
// rc.Billing and the request's cost metadata are rewritten before the one
// SETTLE, so the settlement record, the spend guard's window (it reads
// BilledMicros after this plugin) and the request log carry the same amount.
// The cost source is catalog_max, as for every other charge at a hold, when
// every billed attempt was charged at its hold. When another attempt billed
// from another source, the request's cost_source is mixed and the log's
// (rc.Billing.CostSource, whose enum has no mixed) keeps the cost plugin's,
// as the cost plugin records a mixed request.
func (p *Plugin) chargeAtHold(rc *models.RequestContext) bool {
	b := &rc.Billing
	if b.OwnedRequest || b.InclusiveDispatched {
		return false
	}
	attempts := rc.AttemptsSnapshot()
	latest := len(attempts) - 1
	for latest >= 0 && (attempts[latest].Outcome == models.OutcomeSkipped || attempts[latest].Outcome == models.OutcomeCircuitOpen) {
		latest--
	}
	marked := rc.Metadata[metaUsageUnreadable] == UsageDispatchedWithoutUsage
	markupBps := p.settings.markupBps()
	charged, mixed := false, false
	for i := range attempts {
		a := &attempts[i]
		hold := attemptHold(a, i == latest, marked, b.AttemptHoldMicros)
		if hold <= 0 {
			mixed = mixed || (a.Billable && a.CostSource != models.CostSourceCatalogMax)
			continue
		}
		upstream := hold * 10000 / int64(10000+markupBps)
		b.BilledMicros += hold - a.BilledCostMicros
		b.UpstreamMicros += upstream - a.UpstreamCostMicros
		b.LandedMicros += upstream - a.LandedCostMicros
		rc.UpdateAttempt(a.AttemptID, func(x *models.Attempt) {
			x.Billable, x.UsageEstimated, x.CostSource = true, true, models.CostSourceCatalogMax
			x.BilledCostMicros, x.UpstreamCostMicros, x.LandedCostMicros = hold, upstream, upstream
		})
		charged = true
	}
	if !charged {
		return false
	}
	b.MarkupMicros, b.MarkupBps = b.BilledMicros-b.UpstreamMicros, markupBps
	b.UsageEstimated = true
	source := models.CostSourceMixed
	if !mixed {
		source = models.CostSourceCatalogMax
		b.CostSource = source
	}
	rc.Metadata["cost"] = microsUSD(b.BilledMicros)
	rc.Metadata["cost_base"] = microsUSD(b.UpstreamMicros)
	rc.Metadata["cost_markup_percent"] = strconv.FormatFloat(float64(markupBps)/100, 'f', -1, 64)
	rc.Metadata["cost_source"] = string(source)
	rc.Metadata["usage_estimated"] = "true"
	return true
}

// attemptHold is the hold chargeAtHold charges a at, 0 when a is not charged
// at a hold: a managed attempt sent and left without usage (flagged, or the
// request's latest attempt, latest, on a marked request), not rejected by the
// provider, without reported usage. It is the hold snapshotted on a, or, for
// the latest attempt without a snapshot, the request's latest hold
// (latestHold).
func attemptHold(a *models.Attempt, latest, marked bool, latestHold int64) int64 {
	if !a.DispatchedWithoutUsage && !(latest && marked) {
		return 0
	}
	if a.CredentialType != models.CredentialManaged || a.ProviderRejected || reportedUsage(a.Usage) {
		return 0
	}
	if a.Pricing != nil && a.Pricing.HoldMicros > 0 {
		return a.Pricing.HoldMicros
	}
	if latest {
		return latestHold
	}
	return 0
}

// reportedUsage reports whether u is usage the upstream reported: not a
// gateway estimate, with both token counts measured or a reported cost.
func reportedUsage(u *models.Usage) bool {
	return u != nil && !u.Estimated && !u.NeedsTokenEstimate()
}

// microsUSD renders micros as USD with six decimals, exactly, as the cost
// plugin writes the request's cost: 15782 is "0.015782".
func microsUSD(m int64) string {
	sign := ""
	if m < 0 {
		sign, m = "-", -m
	}
	return fmt.Sprintf("%s%d.%06d", sign, m/1_000_000, m%1_000_000)
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
