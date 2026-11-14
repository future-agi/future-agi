package budget

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An org_custom cost of 1e13 USD (custom pricing has no upper bound) must not
// wrap into a negative spend that unblocks the org for the rest of the period.
func TestPerOrg_HugeCostKeepsBlocking(t *testing.T) {
	store := testTenantStore("org-huge", &tenant.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		OrgLimit:      100.0,
		HardLimit:     true,
	})
	p := New(nil, testPricing(), false, store)
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"key_org_id":  "org-huge",
		"cost":        "10000000000000",
		"cost_source": "org_custom",
	})
	rc.Response = &models.ChatCompletionResponse{}
	p.ProcessResponse(context.Background(), rc)

	rc2 := makeRC("gpt-4o", "openai", map[string]string{"key_org_id": "org-huge"})
	if result := p.ProcessRequest(context.Background(), rc2); result.Error == nil || result.Error.Code != "org_budget_exceeded" {
		t.Fatalf("after a 1e13 charge: %+v; want org_budget_exceeded", result.Error)
	}
}
