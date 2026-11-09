package routerbilling

import (
	"errors"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const PendingLabel = "estimated, pending reconciliation"

var ErrEvidence = errors.New("router billing: incomplete or conflicting generation evidence")

// Decision is the monetary decision for ONE already-dispatched attempt. It is
// projected into a single request spend, not sent to settle_spend on its own.
// Estimated upstream/fee amounts are labelled estimates, never actuals.
type Decision struct {
	AttemptID                    string
	Plan                         Plan
	Generation                   models.GenerationEvidence
	UpstreamMicros, BilledMicros int64
	Managed, Estimated, Pending  bool
	Label, Anomaly               string
	Quarantine                   bool
}

// Decide accepts only the original provider decimal, not the mutable Cost float
// later rewritten for the client. Missing/invalid cost consumes the FULL hold;
// no token-price fallback or automatic waiver is permitted for these routers.
//
// Over-hold policy: settle the actual charge (the hot-path Ledger explicitly
// allows overshoot), record the anomaly and quarantine the exact listing. The
// durable adapter must persist that quarantine atomically with the decision.
func Decide(attemptID string, plan Plan, generation models.GenerationEvidence, costDecimal string, managed bool) (Decision, error) {
	if attemptID == "" || !FamilyMember(plan.listing) || plan.hold <= 0 {
		return Decision{}, ErrBound
	}
	d := Decision{AttemptID: attemptID, Plan: plan, Generation: generation, Managed: managed}
	upstream, billed, err := Charge(costDecimal)
	if err != nil {
		if !managed {
			return d, nil // BYOK never reserves, debits credits, or adds a fee
		}
		d.UpstreamMicros, d.BilledMicros = plan.upstream, plan.hold
		d.Estimated, d.Pending, d.Quarantine = true, true, true
		d.Label, d.Anomaly = PendingLabel, "missing_or_invalid_upstream_cost"
	} else {
		d.UpstreamMicros = upstream
		if managed {
			d.BilledMicros = billed
			if billed > plan.hold {
				d.Quarantine, d.Anomaly = true, "actual_charge_exceeds_hold"
			}
		}
	}
	if managed && (generation.ID == "" || generation.ServedModel == "" || generation.Conflict) {
		d.Quarantine = true
		if d.Anomaly == "" {
			d.Anomaly = "missing_or_conflicting_served_identity"
		}
	}
	return d, nil
}
