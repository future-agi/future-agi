package billing

import (
	"testing"
	"time"
)

func TestMixedRecoveryPreservesUnresolvedAttemptProvenance(t *testing.T) {
	prior := AttemptSummary{AttemptID: "r.1", Model: "token", Provider: "original-managed-provider", CredentialType: "managed", Kind: "retry", Outcome: "error"}
	o := OwnedRequest{Record: Settlement{Provider: "later-managed-provider", Attempts: []AttemptSummary{prior}}, Attempts: map[string]OwnedAttempt{
		"r.1": {Model: "token", Micros: 100, Dispatched: true, TokenPriced: true},
		"r.2": {Model: "inclusive", Micros: 200, Dispatched: true},
	}}
	got := o.RecoveryRecord("org", "r", time.Unix(0, 0))
	if got.Attempts[0].Provider != prior.Provider || got.Attempts[0].Kind != prior.Kind || got.Attempts[0].BilledMicros != 100 || got.BilledMicros != 300 {
		t.Fatalf("recovery replaced prior provenance: %+v", got)
	}
}
