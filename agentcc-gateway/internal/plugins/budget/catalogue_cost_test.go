package budget

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A budget counts the price the cost plugin puts on the request, from the
// catalogue. gpt-5.5 is not in the budget's own price table, which used to
// count it as free, so no budget on it ever stopped a request.
func TestBudgetCountsTheCataloguePrice(t *testing.T) {
	for _, model := range []string{"gpt-5.5", "gpt-4o-mini"} {
		t.Run(model, func(t *testing.T) {
			store := tenant.NewStore()
			store.Set("org-b", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, DefaultPeriod: "monthly", OrgLimit: 0.001, HardLimit: true}})
			db := modeldb.New(modeldb.BundledModels, nil)
			budget := New(nil, NewPricingLookup(nil), true, store)
			// Registered in the order the budget checks in, as main.go does.
			engine := pipeline.NewEngine(budget, costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil))

			call := func() error {
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Model, rc.Provider = model, "openai"
				rc.Metadata[tenant.MetadataKeyOrgID] = "org-b"
				return engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
					rc.Response = &models.ChatCompletionResponse{Usage: &models.Usage{PromptTokens: 100_000, CompletionTokens: 10_000}}
					return nil
				})
			}
			if err := call(); err != nil {
				t.Fatalf("first request refused: %v", err)
			}
			err := call()
			apiErr, ok := err.(*models.APIError)
			if !ok || apiErr.Code != "org_budget_exceeded" {
				t.Fatalf("second request: err = %v, want org_budget_exceeded", err)
			}
		})
	}
}
