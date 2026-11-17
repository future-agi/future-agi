package routerbilling

import (
	"context"
	"math"
)

// GenerationClient is deliberately injectable. No HTTP/vendor client is
// installed by this package, and tests must use synthetic generation records.
type GenerationClient interface {
	GenerationCost(context.Context, string) (GenerationCost, error)
}

type GenerationCost struct {
	ID, ServedModel, CostDecimal string
}

// RefundPath adapts the ledger's EXISTING refund(org, request_id, micros,
// reason=..., actor=...) path. The adapter must return the persisted amount,
// including on replay, so a pre-existing, different refund cannot be mistaken
// for this resolution. It must not write a second spend or a custom credit.
type RefundPath interface {
	Refund(ctx context.Context, org, requestID string, micros int64, reason string) (persistedMicros int64, err error)
}

type Resolution struct {
	ActualBilledMicros, RefundMicros, ShortfallMicros int64
	Quarantine                                        []string
}

// Reconcile resolves ALL estimated attempts before issuing the one request
// refund. This is necessary because the pinned refund path is once per request,
// not once per attempt. A failed/missing lookup leaves the original full-hold
// estimated spend intact, even after a deadline. There is no automatic waiver.
//
// The caller must load an immutable, durably committed projection, verify its
// original settle_spend has drained, and durably save the returned resolution.
// Replays call the same idempotent refund path. This accounting core does NOT
// replace that ownership/outbox adapter, which is required before admission.
func Reconcile(ctx context.Context, projection Projection, upstream GenerationClient, refunds RefundPath) (Resolution, error) {
	canonical, err := Project(projection.Record, projection.Evidence)
	if err != nil || canonical.Record.BilledMicros != projection.Record.BilledMicros ||
		canonical.Record.UpstreamMicros != projection.Record.UpstreamMicros || canonical.Record.MarkupMicros != projection.Record.MarkupMicros {
		return Resolution{}, ErrEvidence
	}
	var resolution Resolution
	quarantine := map[string]bool{}
	for _, d := range projection.Evidence {
		if !d.Managed {
			continue
		}
		actual := d.BilledMicros
		if d.Pending {
			if upstream == nil || d.Generation.ID == "" || d.Generation.Conflict {
				return Resolution{}, ErrEvidence
			}
			real, err := upstream.GenerationCost(ctx, d.Generation.ID)
			if err != nil {
				return Resolution{}, err
			}
			if real.ID != d.Generation.ID || real.ServedModel == "" ||
				(d.Generation.ServedModel != "" && real.ServedModel != d.Generation.ServedModel) {
				return Resolution{}, ErrEvidence
			}
			_, actual, err = Charge(real.CostDecimal)
			if err != nil {
				return Resolution{}, err
			}
		}
		if actual < 0 || math.MaxInt64-resolution.ActualBilledMicros < actual {
			return Resolution{}, ErrAmount
		}
		resolution.ActualBilledMicros += actual
		if (d.Quarantine || actual > d.Plan.hold) && !quarantine[d.Plan.listing] {
			resolution.Quarantine = append(resolution.Quarantine, d.Plan.listing)
			quarantine[d.Plan.listing] = true
		}
	}
	settled := projection.Record.BilledMicros
	if settled < 0 || projection.Record.RequestID == "" || projection.Record.OrgID == "" || len(projection.Evidence) == 0 {
		return Resolution{}, ErrEvidence
	}
	if resolution.ActualBilledMicros > settled {
		// A spend already drained is immutable. There is no second spend or
		// silent absorption: persist the shortfall and keep the listing closed.
		resolution.ShortfallMicros = resolution.ActualBilledMicros - settled
		return resolution, nil
	}
	resolution.RefundMicros = settled - resolution.ActualBilledMicros
	if resolution.RefundMicros > 0 {
		if refunds == nil {
			return Resolution{}, ErrEvidence
		}
		amount, err := refunds.Refund(ctx, projection.Record.OrgID, projection.Record.RequestID,
			resolution.RefundMicros, "router generation cost reconciled")
		if err != nil {
			return Resolution{}, err
		}
		if amount != resolution.RefundMicros {
			return Resolution{}, ErrEvidence
		}
	}
	return resolution, nil
}
