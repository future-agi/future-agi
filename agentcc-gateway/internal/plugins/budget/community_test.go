package budget

import (
	"context"
	"testing"

	quotaplugin "github.com/futureagi/agentcc-gateway/internal/plugins/quota"
)

// TH-8084 B2: switching Cloud's commercial quota off on a self-hosted
// install must leave the customer's own budgets enforced.
func TestCustomerBudgetStillEnforcedWithCommercialQuotasOff(t *testing.T) {
	quota := quotaplugin.New(nil, false)
	tracker := testTracker()
	budget := New(tracker, testPricing(), true, nil)
	tracker.RecordSpend("", "alice", "", "gpt-4o", nil, 500.00)

	rc := makeRC("gpt-4o", "openai", map[string]string{"auth_key_owner": "alice"})
	if r := quota.ProcessRequest(context.Background(), rc); r.Error != nil {
		t.Fatalf("commercial quota must be off: %+v", r.Error)
	}
	result := budget.ProcessRequest(context.Background(), rc)
	if result.Error == nil || result.Error.Code != "budget_exceeded" {
		t.Fatalf("customer budget must still block, got %+v", result.Error)
	}
}
