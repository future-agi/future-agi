package agentbudget

import (
	"context"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// finish settles a held call at what it cost, after the cost plugin priced
// it, or voids its hold when it cost nothing (an error before anything was
// billed, a cache hit). Once per call: the finalizer voids a hold nothing
// finished.
func (p *Plugin) finish(rc *models.RequestContext, st *runState) {
	if !st.reserved || !st.done.CompareAndSwap(false, true) {
		return
	}
	cost := callCost(rc)
	if st.strict == nil && cost == 0 && !isManaged(rc) && served(rc) && st.estPriced && st.estMicros > 0 {
		// A BYOK call whose upstream cost is unknown counts at its catalog
		// worst case, marked estimated.
		cost = st.estMicros
		rc.Billing.UsageEstimated = true
		rc.Metadata["usage_estimated"] = "true"
	}
	ctx := context.Background()
	var res redisstate.AgentBudgetState
	var err error
	if st.strict != nil {
		var known bool
		cost, known = st.strict.ActualSpend(rc)
		if !known {
			defer st.done.Store(false) // a later finish may carry authoritative usage
			rc.Metadata["run_budget_state"] = "uncertain"
			if err := p.deps.Store.FenceStrict(ctx, st.org, st.scope, st.holdID); err != nil {
				slog.Error("strict run uncertainty fence unavailable; reservation retained", "org_id", st.org, "scope", st.scope, "error", err)
			}
			return
		}
		res, err = p.deps.Store.Complete(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL)
		if err != nil {
			res, err = p.deps.Store.Complete(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL)
		}
	} else if cost > 0 {
		// A SETTLE that fails is retried once. It may have applied, and then
		// counts twice: a run budget errs on the side of refusing early.
		if res, err = p.scopes.Settle(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL); err != nil {
			res, err = p.scopes.Settle(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL)
		}
	} else {
		res, err = p.scopes.Void(ctx, st.org, st.scope, st.holdID, st.pol.scopeTTL)
	}
	if err != nil {
		if st.strict != nil {
			// A refused actual must block new work now, not merely leave a
			// quote-sized hold until expiry. A later valid actual can retry.
			defer st.done.Store(false)
			rc.Metadata["run_budget_state"] = "uncertain"
			if fenceErr := p.deps.Store.FenceStrict(ctx, st.org, st.scope, st.holdID); fenceErr != nil {
				slog.Error("strict run uncertainty fence unavailable; reservation retained", "org_id", st.org, "scope", st.scope, "error", fenceErr)
			}
		}
		slog.Error("run budget settlement unavailable; strict reservations remain held",
			"request_id", rc.RequestID, "org_id", st.org, "scope", st.scope, "cost_micros", cost, "error", err)
		return
	}
	if res.HasLimit() {
		recordRemaining(rc, res.RemainingMicros())
	}
}

// callCost is what the call cost its run, in micros: the sum over its
// billable attempts of the billed amount for a managed attempt and the
// upstream cost for a BYOK one, else, when it recorded no attempt that was
// sent (none at all, or only skipped ones), the request's own billed
// (managed) or upstream (BYOK) cost, which the cost plugin then priced as
// the one attempt the request made. A cache hit costs nothing.
func callCost(rc *models.RequestContext) int64 {
	if rc.Flags.CacheHit {
		return 0
	}
	attempts := rc.AttemptsSnapshot()
	if !anySent(attempts) {
		if isManaged(rc) {
			return max(rc.Billing.BilledMicros, 0)
		}
		return max(rc.Billing.UpstreamMicros, 0)
	}
	var sum int64
	for i := range attempts {
		a := &attempts[i]
		switch {
		case !a.Billable:
		case a.CredentialType == models.CredentialManaged:
			sum += max(a.BilledCostMicros, 0)
		default:
			sum += max(a.UpstreamCostMicros, 0)
		}
	}
	return sum
}

// anySent reports whether any of attempts was sent: a skipped attempt never
// was, and never carries the request's charge.
func anySent(attempts []models.Attempt) bool {
	for i := range attempts {
		if attempts[i].Outcome != models.OutcomeSkipped {
			return true
		}
	}
	return false
}

// served reports whether an upstream answered the call.
func served(rc *models.RequestContext) bool {
	return len(rc.Errors) == 0 && !rc.Flags.ShortCircuited && !rc.Flags.CacheHit
}
