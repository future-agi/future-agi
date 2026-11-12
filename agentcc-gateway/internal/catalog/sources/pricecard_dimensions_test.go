package sources

import (
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

func TestCompletePricingDirectCardDimensions(t *testing.T) {
	for _, tc := range []struct {
		price    string
		billable bool
	}{
		{`{"prompt":"0.000002"}`, false},
		{`{"completion":"0.000010"}`, false},
		{`{"prompt":"0.000002","request":"0.04"}`, false},
		{`{"prompt":"0.000002","completion":"0"}`, true},
		{`{"request":"0.04"}`, true},
	} {
		t.Run(tc.price, func(t *testing.T) {
			card, err := ParsePriceCard([]byte(fmt.Sprintf(`{"provider":"acme","upstream_slug":"acme","as_of":"2026-10-26","source_url":"https://example.test/pricing","data_collection":"deny","models":[{"id":"acme/model","prices":%s}]}`, tc.price)))
			if err != nil {
				t.Fatal(err)
			}
			m := card.Snapshot("acme", time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC)).Models["acme/model"]
			if got := m.ManagedBillable(catalog.SourcePriceCard); got != tc.billable {
				t.Fatalf("billable=%t want=%t", got, tc.billable)
			}
		})
	}
}
