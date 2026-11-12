package routerbilling

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

func TestRouterBYOKCannotProjectCreditSpend(t *testing.T) {
	for _, id := range routers {
		d, err := Decide("request.1", testPlan(t, id), testGeneration(), "0.01", false)
		if err != nil {
			t.Fatal(err)
		}
		_, err = Project(billing.Settlement{RequestID: "request", OrgID: "org", Attempts: []billing.AttemptSummary{{AttemptID: "request.1", Model: id, CredentialType: "byok"}}}, []Decision{d})
		if err == nil {
			t.Fatalf("%s BYOK request projected a financial spend", id)
		}
	}
}

func TestRouterReconciliationRejectsConflictingProjectionAndRefund(t *testing.T) {
	d, _ := Decide("request.1", testPlan(t, routers[0]), testGeneration(), "", true)
	p := testProjection(t, d)
	client := &fakeUpstream{rows: map[string]GenerationCost{"gen-synthetic": {ID: "gen-synthetic", ServedModel: "test/served", CostDecimal: "0.000004"}}}
	refund := &fakeRefund{credited: 5} // a different request-level refund already won
	if _, err := Reconcile(context.Background(), p, client, refund); err == nil {
		t.Fatal("conflicting persisted refund was reported as reconciled")
	}
	p.Record.BilledMicros++
	calls := len(client.calls)
	if _, err := Reconcile(context.Background(), p, client, refund); err == nil || len(client.calls) != calls {
		t.Fatal("conflicting projection was trusted for a refund")
	}
}
