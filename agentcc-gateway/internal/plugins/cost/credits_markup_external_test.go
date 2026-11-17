package cost_test

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

// Keep this cross-plugin contract external: credits now reuses the cost pricer
// for journal snapshots, so an internal cost test cannot import credits.
func TestMarkupDrift_CreditsHoldAtTheCatalogsMarkup(t *testing.T) {
	want := 500
	if path := os.Getenv("PRICE_CATALOG_FILE"); path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var doc struct {
			ModelPricing struct {
				MarkupBps *struct {
					Value *int `json:"value"`
				} `json:"markup_bps"`
			} `json:"model_pricing"`
		}
		if err := json.Unmarshal(raw, &doc); err != nil {
			t.Fatal(err)
		}
		if doc.ModelPricing.MarkupBps == nil || doc.ModelPricing.MarkupBps.Value == nil {
			t.Fatal("missing model_pricing.markup_bps.value")
		}
		want = *doc.ModelPricing.MarkupBps.Value
	}
	if got := int(math.Round(credits.DefaultSettings().MarkupPercent * 100)); got != want {
		t.Fatalf("credits markup=%d want=%d", got, want)
	}
}
