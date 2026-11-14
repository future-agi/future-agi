package budget

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// reportedBudgetCall exercises the same pre-check -> provider -> cost -> budget
// ordering used by the gateway. Parse usage as JSON so vendor fields enter Extra.
func reportedBudgetCall(t *testing.T, engine *pipeline.Engine, promptTokens int, reported float64) (map[string]string, error) {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model, rc.Provider = "sonar", "perplexity"
	rc.Metadata[tenant.MetadataKeyOrgID] = "reported-budget-org"
	err := engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
		var usage models.Usage
		body := fmt.Sprintf(`{"prompt_tokens":%d,"completion_tokens":0,"cost":{"currency":"USD","input_cost":0,"total_cost":%.6f}}`, promptTokens, reported)
		if err := json.Unmarshal([]byte(body), &usage); err != nil {
			return err
		}
		if len(usage.Extra["cost"]) == 0 {
			t.Fatal("JSON usage lost the provider's cost object")
		}
		rc.Response = &models.ChatCompletionResponse{Usage: &usage}
		return nil
	})
	metadata := make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		metadata[k] = v
	}
	return metadata, err
}

func testBudgetReportedCost(t *testing.T, tokens int, reported, limit float64, catalogueAboveLimit, wantRefused bool) {
	t.Helper()
	store := tenant.NewStore()
	store.Set("reported-budget-org", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{
		Enabled: true, DefaultPeriod: "monthly", OrgLimit: limit, HardLimit: true,
	}})
	db := modeldb.New(modeldb.BundledModels, nil)
	catalogue, priced := db.CalculateCost("perplexity/sonar", tokens, 0, modeldb.CostOptions{})
	if !priced || (catalogue > limit) != catalogueAboveLimit {
		t.Fatalf("test precondition: catalogue cost = %v (priced=%v), org limit = %v, want catalogue above limit=%v", catalogue, priced, limit, catalogueAboveLimit)
	}
	budget := New(nil, NewPricingLookup(nil), true, store)
	cost := costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	cost.SetReportedCostVendors(func(*models.RequestContext) string { return "perplexity" })
	engine := pipeline.NewEngine(budget, cost)

	if metadata, err := reportedBudgetCall(t, engine, tokens, reported); err != nil {
		t.Fatalf("first request refused: %v", err)
	} else {
		if got, want := metadata["cost"], fmt.Sprintf("%.6f", reported); got != want {
			t.Errorf("cost = %q, want reported %q", got, want)
		}
		if got := metadata["cost_source"]; got != costplugin.CostSourceProviderReported {
			t.Errorf("cost_source = %q, want provider_reported", got)
		}
		if got, want := metadata[costplugin.MetadataCatalogue], fmt.Sprintf("%.6f", catalogue); got != want {
			t.Errorf("cost_catalogue = %q, want %q", got, want)
		}
	}

	_, err := reportedBudgetCall(t, engine, tokens, reported)
	if wantRefused {
		apiErr, ok := err.(*models.APIError)
		if !ok || apiErr.Code != "org_budget_exceeded" {
			t.Fatalf("next request: err = %v, want org_budget_exceeded", err)
		}
	} else if err != nil {
		t.Fatalf("next request refused despite reported spend below limit: %v", err)
	}
}

func TestBudgetReportedCostAboveLimitBlocksNextRequest(t *testing.T) {
	// 1,000 sonar input tokens list at $0.001, below the $0.002 limit.
	testBudgetReportedCost(t, 1_000, 0.003, 0.002, false, true)
}

func TestBudgetReportedCostBelowLimitAllowsNextRequest(t *testing.T) {
	// 10,000 input tokens list at $0.01, above the $0.002 limit.
	testBudgetReportedCost(t, 10_000, 0.0005, 0.002, true, false)
}

func TestBudgetReportedZeroDoesNotSpend(t *testing.T) {
	// A vendor-reported zero is an actual zero, even for expensive usage.
	testBudgetReportedCost(t, 10_000, 0, 0.002, true, false)
}
