package budget

import (
	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Refusal attaches only server-owned policy identity. A bucket-specific ledger
// result must match an applicable configured bucket; never echo arbitrary ledger
// text or caller metadata. For ledger-wide failures, use the first applicable
// daily bucket (the same order as Reserve), then the run policy.
func (a *Admission) Refusal(err *models.APIError, kind, level string) *models.APIError {
	if err == nil || a == nil {
		return err
	}
	if kind == "" {
		kind = "daily"
		if a.agentLimit < 0 && a.keyLimit < 0 {
			kind = "run"
		}
	}
	scope, ref := "key", a.key
	if kind == "run" {
		if a.agent != "" {
			scope, ref = "agent", a.agent
		}
	} else if a.agentLimit >= 0 && (level != "key_id:"+a.key || a.keyLimit < 0) {
		scope, ref = "agent", a.agent
	}
	copy := *err
	copy.Metadata = contract.AdmissionCapRefusalMetadata{
		CapScope: scope, CapRef: contract.AdmissionCapIdentity(ref),
		CapKind: kind, PolicyRevision: a.revision,
	}
	return &copy
}

// RunRefusal is for a refusal attributed to the administrative run limit by
// its caller, or unavailable admission state. It is not a blanket adapter for
// legacy caller budgets, per-call caps, or run-stop reasons.
func (a *Admission) RunRefusal(err *models.APIError) *models.APIError {
	if a == nil || err == nil {
		return err
	}
	switch err.Code {
	case "agent_budget_exhausted", "call_cap_unenforceable":
		if a.runLimit < 0 {
			return err
		}
	case "budget_state_unavailable":
	default:
		return err
	}
	copy := *err
	kind := "run"
	copy.Message = "run admission-cap policy cannot admit this request"
	if a.runLimit < 0 {
		kind = ""
		copy.Message = "admission-cap state cannot be checked right now"
	}
	copy.Param, copy.RetryAfterSec = nil, 0
	return a.Refusal(&copy, kind, "")
}
