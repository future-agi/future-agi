package routerbilling

import (
	"math"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// Projection keeps evidence separate from the CLOSED v1 financial record. The
// durable adapter must atomically persist both before releasing hold ownership.
// The drain calls settle_spend(Record, workspace_id=request_log.workspace or None)
// exactly once under Record.RequestID. It must not emit another usage event:
// The ledger owns funding, attribution, catalogue version and the rated event.
// Existing agent/key/audit/timestamp fields are preserved from the base record.
type Projection struct {
	Record   billing.Settlement
	Evidence []Decision
}

// Project aggregates already-rounded attempt charges; never adds another fee,
// emits child spends, invents request IDs, or overwrites catalogue snapshot with
// the ledger's independent active price_catalog_version.
func Project(base billing.Settlement, decisions []Decision) (Projection, error) {
	if base.RequestID == "" || base.OrgID == "" || len(decisions) == 0 || len(base.Attempts) != len(decisions) {
		return Projection{}, ErrEvidence
	}
	base.Attempts = append([]billing.AttemptSummary(nil), base.Attempts...)
	base.BilledMicros, base.UpstreamMicros, base.MarkupMicros = 0, 0, 0
	base.UsageEstimated = false
	base.Kind, base.GenerationID, base.MarkupBps = billing.KindSpend, base.RequestID, 500
	seen := make(map[string]bool, len(decisions))
	var held int64
	for i, d := range decisions {
		a := &base.Attempts[i]
		if d.AttemptID == "" || seen[d.AttemptID] || a.AttemptID != d.AttemptID || a.Model != d.Plan.listing ||
			d.Managed != (a.CredentialType == "managed") || d.BilledMicros < 0 || d.UpstreamMicros < 0 {
			return Projection{}, ErrEvidence
		}
		seen[d.AttemptID] = true
		a.UpstreamMicros, a.BilledMicros = d.UpstreamMicros, d.BilledMicros
		if !d.Managed {
			if d.BilledMicros != 0 {
				return Projection{}, ErrEvidence
			}
			continue
		}
		if d.BilledMicros < d.UpstreamMicros || d.Plan.hold <= 0 || math.MaxInt64-held < d.Plan.hold ||
			math.MaxInt64-base.BilledMicros < d.BilledMicros || math.MaxInt64-base.UpstreamMicros < d.UpstreamMicros {
			return Projection{}, ErrAmount
		}
		held += d.Plan.hold
		base.BilledMicros += d.BilledMicros
		base.UpstreamMicros += d.UpstreamMicros
		base.UsageEstimated = base.UsageEstimated || d.Estimated
	}
	if held == 0 {
		return Projection{}, ErrEvidence // BYOK-only requests never produce credit spends
	}
	if base.HoldMicros != held {
		return Projection{}, ErrBound // each hold has one owner; none is silently lost
	}
	base.MarkupMicros = base.BilledMicros - base.UpstreamMicros
	base.CostSource = "provider_reported"
	if base.UsageEstimated {
		base.CostSource = "catalog_max" // sidecar carries the explicit PendingLabel
	}
	return Projection{Record: base, Evidence: append([]Decision(nil), decisions...)}, nil
}
