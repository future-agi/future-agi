package credits

import (
	"context"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The gateway marks a managed request whose latest attempt was sent upstream
// and ended with no usage (request metadata usage_unreadable =
// dispatched_without_usage). finishManaged then settles it once at the hold
// that attempt reserved, never voids it; anything else keeps its behaviour.

const (
	atHoldMarkerKey   = "usage_unreadable"
	atHoldMarkerValue = "dispatched_without_usage"
)

// heldManagedRequest is managedRequest held on g's ledger, with one recorded
// managed attempt that ended as outcome, priced at the hold it reserved.
func heldManagedRequest(t *testing.T, g *managedGateway, id string, outcome models.AttemptOutcome, class models.ErrorClass) *models.RequestContext {
	t.Helper()
	rc := managedRequest(id, 1000)
	if res := g.plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatal(res.Error)
	}
	if !rc.Billing.Reserved || rc.Billing.AttemptHoldMicros <= 0 {
		t.Fatalf("the request was not held (reserved %v, hold %d)", rc.Billing.Reserved, rc.Billing.AttemptHoldMicros)
	}
	rc.AddAttempt(models.Attempt{
		Kind: models.AttemptPrimary, Provider: rc.Provider, Model: rc.Request.Model,
		CredentialType: models.CredentialManaged, Outcome: outcome, ErrorClass: class,
		ErrorOrigin: models.OriginClient, Pricing: models.NewAttemptPricing(&rc.Billing),
	})
	return rc
}

func (g *managedGateway) records(t *testing.T) []map[string]string {
	t.Helper()
	return g.mem.Outbox(testOrg)
}

// A dispatched call with no usage settles once at its attempt's hold, with
// the charge visible on the request (cost, cost source catalog_max).
func TestDispatchedCallWithoutUsageSettlesAtItsHold(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := heldManagedRequest(t, g, "req-hold-1", models.OutcomeCancelled, models.ErrClassCancelled)
	defer rc.Release()
	hold := rc.Billing.AttemptHoldMicros
	rc.Metadata[atHoldMarkerKey] = atHoldMarkerValue

	g.plugin.ProcessResponse(context.Background(), rc)

	recs := g.records(t)
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(hold, 10) {
		t.Fatalf("records = %v, want one spend at the hold %d", recs, hold)
	}
	if want := strconv.FormatInt(hold*10000/10500, 10); recs[0]["upstream_micros"] != want {
		t.Errorf("upstream_micros = %s, want %s (the hold without the 5%% markup it was reserved with)", recs[0]["upstream_micros"], want)
	}
	if recs[0]["cost_source"] != "catalog_max" || recs[0]["usage_estimated"] != "1" {
		t.Errorf("cost_source=%q usage_estimated=%q, want catalog_max and 1", recs[0]["cost_source"], recs[0]["usage_estimated"])
	}
	if a := g.account(t); a.SpentMicros != hold || a.HeldMicros != 0 {
		t.Fatalf("account spent=%d held=%d, want spent %d and nothing held", a.SpentMicros, a.HeldMicros, hold)
	}
	if rc.Billing.BilledMicros != hold || string(rc.Billing.CostSource) != "catalog_max" || !rc.Billing.UsageEstimated {
		t.Errorf("billing = billed %d source %q estimated %v, want the hold, catalog_max, estimated (the spend guard window reads BilledMicros after credits)",
			rc.Billing.BilledMicros, rc.Billing.CostSource, rc.Billing.UsageEstimated)
	}
	if got, want := rc.Metadata["cost"], strconv.FormatFloat(float64(hold)/1e6, 'f', 6, 64); got != want {
		t.Errorf("cost metadata = %q, want %q (what the request log shows is what settled)", got, want)
	}
	if rc.Metadata["cost_source"] != "catalog_max" || rc.Metadata["usage_estimated"] != "true" {
		t.Errorf("cost_source metadata = %q usage_estimated = %q", rc.Metadata["cost_source"], rc.Metadata["usage_estimated"])
	}
}

// (d) Usage that arrives after an at-hold settle never settles again, neither
// on the same request nor on a replay of its id.
func TestLateUsageAfterAtHoldSettleDoesNotSettleAgain(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := heldManagedRequest(t, g, "req-hold-late", models.OutcomeTimeout, models.ErrClassTimeout)
	defer rc.Release()
	hold := rc.Billing.AttemptHoldMicros
	rc.Metadata[atHoldMarkerKey] = atHoldMarkerValue
	g.plugin.ProcessResponse(context.Background(), rc)

	// The usage turns up late: priced, and the marker gone.
	delete(rc.Metadata, atHoldMarkerKey)
	rc.Billing.BilledMicros = 9_000
	g.plugin.ProcessResponse(context.Background(), rc)

	replay := managedRequest("req-hold-late", 1000)
	defer replay.Release()
	replay.Billing.Reserved = true
	replay.Billing.HoldMicros, replay.Billing.AttemptHoldMicros = hold, hold
	replay.Billing.BilledMicros = 9_000
	g.plugin.ProcessResponse(context.Background(), replay)

	recs := g.records(t)
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(hold, 10) {
		t.Fatalf("records = %v, want the one at-hold spend only", recs)
	}
	if a := g.account(t); a.SpentMicros != hold || a.HeldMicros != 0 {
		t.Fatalf("account spent=%d held=%d, want spent exactly the hold %d once", a.SpentMicros, a.HeldMicros, hold)
	}
}

// (c) Without the gateway's dispatch marker (refused before send, a never
// sent call) a request that billed nothing is voided, as today.
func TestUnmarkedCallWithoutUsageIsStillVoided(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := heldManagedRequest(t, g, "req-hold-none", models.OutcomeCancelled, models.ErrClassCancelled)
	defer rc.Release()

	g.plugin.ProcessResponse(context.Background(), rc)

	recs := g.records(t)
	if len(recs) != 1 || recs[0]["kind"] != "void" {
		t.Fatalf("records = %v, want one void", recs)
	}
	if a := g.account(t); a.SpentMicros != 0 || a.HeldMicros != 0 {
		t.Fatalf("account spent=%d held=%d, want nothing spent", a.SpentMicros, a.HeldMicros)
	}
}

// A marked request whose latest attempt the provider definitively rejected,
// or ran on the caller's own credentials, is not charged at the hold.
func TestAtHoldNeedsAManagedUnansweredAttempt(t *testing.T) {
	for _, tc := range []struct {
		name string
		edit func(a *models.Attempt)
	}{
		{"provider rejected", func(a *models.Attempt) { a.ProviderRejected = true }},
		{"own credentials", func(a *models.Attempt) { a.CredentialType = models.CredentialBYOK }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newManagedGateway(t, 1_000_000)
			rc := heldManagedRequest(t, g, "req-hold-"+tc.name, models.OutcomeError, models.ErrClassUpstream5xx)
			defer rc.Release()
			attempts := rc.AttemptsSnapshot()
			rc.UpdateAttempt(attempts[len(attempts)-1].AttemptID, tc.edit)
			rc.Metadata[atHoldMarkerKey] = atHoldMarkerValue

			g.plugin.ProcessResponse(context.Background(), rc)

			if recs := g.records(t); len(recs) != 1 || recs[0]["kind"] != "void" {
				t.Fatalf("records = %v, want one void", recs)
			}
			if a := g.account(t); a.SpentMicros != 0 {
				t.Fatalf("spent %d, want 0", a.SpentMicros)
			}
		})
	}
}

// A marked attempt whose usage the upstream did report keeps today's settle
// at the billed amount.
func TestAtHoldKeepsReportedUsage(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := heldManagedRequest(t, g, "req-hold-usage", models.OutcomeCancelled, models.ErrClassCancelled)
	defer rc.Release()
	prompt, completion := 10, 20
	attempts := rc.AttemptsSnapshot()
	rc.UpdateAttempt(attempts[len(attempts)-1].AttemptID, func(a *models.Attempt) {
		a.Usage = models.NewUsage(&prompt, &completion)
		a.BilledCostMicros = 347
	})
	rc.Billing.BilledMicros = 347
	rc.Metadata[atHoldMarkerKey] = atHoldMarkerValue

	g.plugin.ProcessResponse(context.Background(), rc)

	recs := g.records(t)
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != "347" {
		t.Fatalf("records = %v, want one spend at the reported 347", recs)
	}
}
