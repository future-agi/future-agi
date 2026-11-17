package budget

import (
	"testing"

	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCapRefusalRunAdapterScope(t *testing.T) {
	for _, limit := range []int64{-1, 5000} {
		a := &Admission{key: "key-a", agent: "worker", agentLimit: 10000, keyLimit: -1, runLimit: limit, revision: 7}
		for _, code := range []string{"agent_call_cap_exceeded", "agent_run_stopped", "other_error"} {
			err := &models.APIError{Status: 402, Code: code, Message: "original reason", Metadata: models.PaymentMeta{Reason: "tool_call_blocked:shell"}}
			if got := a.RunRefusal(err); got != err {
				t.Fatalf("non-admission error was rewritten: %+v", got)
			}
		}
		err := &models.APIError{Status: 503, Code: "budget_state_unavailable", Message: "legacy caller scope", Metadata: models.PaymentMeta{Scope: "run:caller-run"}}
		got := a.RunRefusal(err)
		md, ok := got.Metadata.(contract.AdmissionCapRefusalMetadata)
		kind := "daily"
		if limit >= 0 {
			kind = "run"
		}
		if !ok || md.CapKind != kind || md.CapScope != "agent" || md.CapRef != "worker" || md.PolicyRevision != 7 {
			t.Fatalf("state identity: %+v", got)
		}
		if err.Message != "legacy caller scope" {
			t.Fatal("mutated input error")
		}
		if limit < 0 {
			err := &models.APIError{Status: 402, Code: "agent_budget_exhausted", Message: "caller budget"}
			if a.RunRefusal(err) != err {
				t.Fatal("caller budget became administrative run policy")
			}
		}
	}
}
