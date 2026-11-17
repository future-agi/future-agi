package credits

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A platform-funded key's managed call is not held on, or settled to, the
// org's managed credit, and writes no settlement record; an ordinary managed
// call still holds and settles.
func TestPlatformFundedManagedCallIsNotHeldOrSettled(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-pf", 100)
	rc.Metadata["key_billing"] = "platform_funded"
	if res := g.plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("a platform-funded call was refused: %v", res.Error)
	}
	if a := g.account(t); a.HeldMicros != 0 || rc.Billing.Reserved {
		t.Fatalf("held %d (reserved %v) for a platform-funded call, want nothing held", a.HeldMicros, rc.Billing.Reserved)
	}
	rc.Billing.BilledMicros, rc.Billing.UpstreamMicros = 5_670, 5_400
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openrouter", Model: "anthropic/claude-sonnet-5.5",
		CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess, UpstreamCostMicros: 5_400, BilledCostMicros: 5_670})
	g.plugin.ProcessResponse(context.Background(), rc)
	rc.Release()
	if a := g.account(t); a.SpentMicros != 0 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v after a platform-funded call, want nothing spent or held", a)
	}
	if out := g.mem.Outbox(testOrg); len(out) != 0 {
		t.Fatalf("outbox = %v, want no settlement record for a platform-funded call", out)
	}

	ordinary := managedRequest("req-ordinary", 100)
	defer ordinary.Release()
	if res := g.plugin.ProcessRequest(context.Background(), ordinary); res.Error != nil {
		t.Fatal(res.Error)
	}
	if a := g.account(t); a.HeldMicros == 0 || !ordinary.Billing.Reserved {
		t.Fatalf("an ordinary managed call was not held: %+v", a)
	}
}

// An org with no managed credit left does not refuse a platform-funded call.
func TestPlatformFundedCallNeedsNoOrgCredit(t *testing.T) {
	g := newManagedGateway(t, 0)
	rc := managedRequest("req-pf-empty", 100)
	defer rc.Release()
	rc.Metadata["key_billing"] = "platform_funded"
	if res := g.plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatalf("a platform-funded call was refused for the org's empty credit: %v", res.Error)
	}
}
