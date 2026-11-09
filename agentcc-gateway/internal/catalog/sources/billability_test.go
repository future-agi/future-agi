package sources

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

func TestManagedSourcesRequireRepresentablePrices(t *testing.T) {
	for _, raw := range []string{
		`{"prompt":"bad","completion":"0.01"}`,
		`{"prompt":"NaN","completion":"0.01"}`,
		`{"prompt":"0.01","completion":"0","per_generation":"0.04"}`,
	} {
		t.Run(raw, func(t *testing.T) {
			models, err := ParseOpenAICompatible([]byte(fmt.Sprintf(`{"data":[{"id":"vendor/model","pricing":%s}]}`, raw)), "mock")
			if err != nil {
				t.Fatal(err)
			}
			store := catalog.NewStore()
			store.Put(&catalog.Snapshot{ProviderID: "mock", CatalogSource: catalog.SourceOpenAICompatible, Models: models})
			if store.Load().ServesManaged("mock", "vendor/model") {
				t.Fatal("unrepresentable price admitted")
			}
		})
	}
	for _, raw := range []string{`{"prompt":"NaN","completion":"0.01"}`, `{"prompt":"0.01","per_generation":"0.04"}`} {
		body := fmt.Sprintf(`{"provider":"mock","upstream_slug":"mock","as_of":"2026-10-26","models":[{"id":"model","prices":%s}]}`, raw)
		if _, err := ParsePriceCard([]byte(body)); err == nil {
			t.Errorf("unsupported direct price accepted: %s", raw)
		}
	}
}
