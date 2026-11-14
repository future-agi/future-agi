package credits

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

func testCreditsReportedXAICost(t *testing.T, ticks int64, expectedUSD float64) {
	t.Helper()
	ks := newKeyStore()
	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("missing managed key")
	}
	before := managed.BalanceMicros()
	creditPlugin := New(true, ks)
	db := modeldb.New(modeldb.BundledModels, nil)
	catalogue, priced := db.CalculateCost("xai/grok-3", 10_000, 0, modeldb.CostOptions{})
	if !priced || catalogue <= 0 || catalogue == expectedUSD {
		t.Fatalf("test precondition: xai/grok-3 catalogue cost = %v (priced=%v), want nonzero cost different from reported %v", catalogue, priced, expectedUSD)
	}
	costPlugin := costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	costPlugin.SetReportedCostVendors(func(*models.RequestContext) string { return "xai" })

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()
	rc.Provider, rc.Model = "xai", "grok-3"
	var usage models.Usage
	body := fmt.Sprintf(`{"prompt_tokens":10000,"completion_tokens":0,"cost_in_usd_ticks":%d}`, ticks)
	if err := json.Unmarshal([]byte(body), &usage); err != nil {
		t.Fatal(err)
	}
	if len(usage.Extra["cost_in_usd_ticks"]) == 0 {
		t.Fatal("JSON usage lost the vendor's cost ticks")
	}
	rc.Response = &models.ChatCompletionResponse{Usage: &usage}
	if res := costPlugin.ProcessResponse(context.Background(), rc); res.Action != pipeline.Continue || res.Error != nil {
		t.Fatalf("cost response: %+v", res)
	}
	if got, want := rc.Metadata["cost"], fmt.Sprintf("%.6f", expectedUSD); got != want {
		t.Errorf("cost = %q, want reported %q", got, want)
	}
	if got := rc.Metadata["cost_source"]; got != costplugin.CostSourceProviderReported {
		t.Errorf("cost_source = %q, want provider_reported", got)
	}
	if got, want := rc.Metadata[costplugin.MetadataCatalogue], fmt.Sprintf("%.6f", catalogue); got != want {
		t.Errorf("cost_catalogue = %q, want %q", got, want)
	}
	if got := rc.Metadata[costplugin.MetadataReportedUnit]; got != costplugin.UnitUSDTicks {
		t.Errorf("cost_reported_unit = %q, want %q", got, costplugin.UnitUSDTicks)
	}

	if res := creditPlugin.ProcessResponse(context.Background(), rc); res.Action != pipeline.Continue || res.Error != nil {
		t.Fatalf("credits response: %+v", res)
	}
	if got, want := before-managed.BalanceMicros(), auth.USDToMicros(expectedUSD); got != want {
		t.Errorf("deducted %d micros, want %d micros from reported cost (catalogue would be %v USD)", got, want, catalogue)
	}
	if ticks == 0 {
		if _, exists := rc.Metadata["credits_used"]; exists {
			t.Errorf("zero cost must not mark credits_used, got %q", rc.Metadata["credits_used"])
		}
	} else if got, want := rc.Metadata["credits_used"], fmt.Sprintf("%.6f", expectedUSD); got != want {
		t.Errorf("credits_used = %q, want %q", got, want)
	}
}

func TestCreditsDeductReportedXAICostRatherThanCatalogue(t *testing.T) {
	// 37,756,000 ticks / 1e10 = $0.0037756, rounded to $0.003776 in cost metadata.
	testCreditsReportedXAICost(t, 37_756_000, 0.003776)
}

func TestCreditsReportedZeroDeductsNothing(t *testing.T) {
	testCreditsReportedXAICost(t, 0, 0)
}
