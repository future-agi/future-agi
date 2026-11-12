package billing

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMixedRecoveryKeepsKnownUnresolvedAndUndispatchedSeparate(t *testing.T) {
	known := AttemptSummary{AttemptID: "r.1", Model: "token", Provider: "openrouter", CredentialType: "managed", UpstreamMicros: 50, BilledMicros: 53, Outcome: "error"}
	o := OwnedRequest{Record: Settlement{Provider: "openrouter"}, Attempts: map[string]OwnedAttempt{
		"r.1": {Model: "token", Micros: 100, Dispatched: true, TokenPriced: true, Summary: &known},
		"r.2": {Model: "token", Micros: 200, Dispatched: true, TokenPriced: true},
		"r.3": {Model: "inclusive", Micros: 300, Dispatched: true},
		"r.4": {Model: "inclusive", Micros: 400},
	}}
	got := o.RecoveryRecord("org", "r", time.Unix(0, 0))
	if got.BilledMicros != 553 || got.HoldMicros != 1000 || len(got.Attempts) != 3 || len(got.Dimensional.InclusiveAttempts) != 1 {
		t.Fatalf("lost or billed undispatched liability: %+v", got)
	}
	if got.Attempts[0] != known {
		t.Fatalf("known evidence changed: %+v", got.Attempts[0])
	}
	if got.Attempts[1].BilledMicros != 200 || got.Dimensional.InclusiveAttempts[0].BilledMicros != 300 {
		t.Fatalf("unresolved liability waived: %+v", got)
	}
}

func TestMixedRecoveryRetainsLegacyUnknownLiability(t *testing.T) {
	o := OwnedRequest{LegacyMicros: 100, Record: Settlement{Attempts: []AttemptSummary{{AttemptID: "r.1", CredentialType: "managed", Outcome: "error"}}}, Attempts: map[string]OwnedAttempt{"r.2": {Model: "inclusive", Micros: 200, Dispatched: true}}}
	got := o.RecoveryRecord("org", "r", time.Unix(0, 0))
	if got.BilledMicros != 300 || got.HoldMicros != 300 {
		t.Fatalf("legacy unresolved liability lost: %+v", got)
	}
}

func TestMixedRecoveryKeepsKnownInclusiveEvidence(t *testing.T) {
	evidence := models.InclusiveAttempt{AttemptID: "r.1", Model: "inclusive", HoldMicros: 100, UpstreamMicros: 10, BilledMicros: 11, GenerationID: "synthetic-generation"}
	summary := AttemptSummary{AttemptID: "r.1", Model: "inclusive", CredentialType: "managed", BilledMicros: 11, UpstreamMicros: 10}
	o := OwnedRequest{Attempts: map[string]OwnedAttempt{
		"r.1": {Model: "inclusive", Micros: 100, Dispatched: true, Summary: &summary, Evidence: &evidence},
		"r.2": {Model: "token", Micros: 200, Dispatched: true, TokenPriced: true},
	}}
	got := o.RecoveryRecord("org", "r", time.Unix(0, 0))
	if got.BilledMicros != 211 || got.Dimensional.InclusiveAttempts[0] != evidence {
		t.Fatalf("known inclusive evidence replaced: %+v", got)
	}
}
