package dimensional

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// Dispatch returns evidence even on a partial-generation error. Each invocation
// is one attempt, with a fresh hold ID. Retry/fallback must call Execute again,
// not invoke the dispatch callback twice against one hold. Execute is not wired
// into chat routing: adapter qualification and consumer-v2 rollout come first.
type Dispatch func(context.Context) (Evidence, error)

// Execute acquires a hold before any upstream I/O, then settles once through
// the shared ledger's idempotency boundary. Missing/invalid post-dispatch
// evidence uses the immutable admitted envelope, explicitly an estimate. A
// cancellation before dispatch voids; after dispatch it cannot erase work.
// BYOK skips the credit ledger entirely and does not apply the 5% fee.
func (p *Plan) Execute(ctx context.Context, ledger billing.Ledger, managed bool, rec billing.Settlement, ttl time.Duration, dispatch Dispatch) (Charge, error) {
	if p == nil || dispatch == nil || rec.OrgID == "" || rec.RequestID == "" || ttl <= 0 {
		return Charge{}, fmt.Errorf("invalid dimensional execution context")
	}
	if err := ctx.Err(); err != nil {
		return Charge{}, err
	}
	if !p.dispatched.CompareAndSwap(false, true) {
		return Charge{}, fmt.Errorf("dimensional attempt already executed")
	}
	if managed {
		if ledger == nil {
			return Charge{}, billing.ErrUnavailable
		}
		r, err := ledger.Reserve(ctx, rec.OrgID, rec.RequestID, p.hold, ttl, billing.ReserveNew)
		if err != nil {
			return Charge{}, err
		}
		if r.Status != billing.ReserveOK {
			return Charge{}, fmt.Errorf("dimensional hold refused: %s", r.Status)
		}
	}
	rec.Model, rec.EndpointID, rec.CatalogSnapshotID = p.identity.Model, p.identity.Endpoint, p.identity.Snapshot
	rec.HoldMicros = p.hold
	if err := ctx.Err(); err != nil {
		if managed {
			_, releaseErr := ledger.Void(context.Background(), rec.OrgID, rec.RequestID, rec)
			return Charge{}, errors.Join(err, releaseErr)
		}
		return Charge{}, err
	}
	evidence, dispatchErr := dispatch(ctx)
	charge, priceErr := p.Price(evidence)
	if priceErr != nil {
		// Preserve the protocol violation as an error for the adapter's circuit
		// breaker. The receipt honestly records a bound, never an actual price.
		charge, _ = p.Price(Evidence{})
	}
	if !managed {
		charge.BilledMicros = charge.UpstreamMicros
		return charge, errors.Join(dispatchErr, priceErr)
	}
	rec.UpstreamMicros, rec.BilledMicros = charge.UpstreamMicros, charge.BilledMicros
	rec.MarkupBps, rec.MarkupMicros = 500, charge.BilledMicros-charge.UpstreamMicros
	rec.UsageEstimated, rec.Dimensional = charge.Estimated, &charge.Receipt
	rec.CostSource = "price_card"
	if charge.Receipt.State == "provider_total" {
		rec.CostSource = "provider_reported"
	}
	// Settle explicit free/zero work as a measured spend with zero amounts;
	// Void would lose the distinction between no dispatch and measured zero.
	_, err := ledger.Settle(context.Background(), rec.OrgID, rec.RequestID, rec)
	if err != nil {
		_, err = ledger.Settle(context.Background(), rec.OrgID, rec.RequestID, rec)
	}
	// Never void after a failed settle: its write may already have applied.
	return charge, errors.Join(dispatchErr, priceErr, err)
}
