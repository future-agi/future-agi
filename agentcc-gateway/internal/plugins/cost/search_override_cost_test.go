package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"gopkg.in/yaml.v3"
)

func TestSearchBYOKUsesConfiguredPricesWithoutManagedAdmission(t *testing.T) {
	var overrides map[string]modeldb.ModelOverride
	if err := yaml.Unmarshal([]byte(`
synthetic-search/search:
  pricing:
    input_per_query: 0.017
perplexity/search:
  pricing:
    input_per_query: 0.019
`), &overrides); err != nil {
		t.Fatal(err)
	}
	db := modeldb.New(modeldb.BundledModels, overrides)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tc := range []struct{ provider, cost string }{{"synthetic-search", "0.017000"}, {"perplexity", "0.019000"}} {
		t.Run(tc.provider, func(t *testing.T) {
			rc := searchRC(tc.provider, true)
			defer rc.Release()
			p.ProcessResponse(context.Background(), rc)
			if rc.Metadata["cost"] != tc.cost || rc.Metadata["cost_source"] != string(models.CostSourceModelDB) {
				t.Fatalf("configured search cost = %v, want %s", rc.Metadata, tc.cost)
			}
			if p.Priced("search", modeldb.SearchPriceKey(tc.provider)) {
				t.Fatal("configured BYOK search price opened managed admission")
			}
			if p.Priced("decision", "jev-latest") {
				t.Fatal("decision price opened managed admission")
			}
		})
	}
}
