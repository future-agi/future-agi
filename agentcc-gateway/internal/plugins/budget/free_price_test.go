package budget

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestFreeCatalogueBudgetRate(t *testing.T) {
	for _, tc := range []struct {
		name    string
		pricing *PricingLookup
		want    float64
	}{
		{"no-table", nil, 0},
		{"no-rate", NewPricingLookup(nil), 0},
		{"explicit-rate", NewPricingLookup(map[string]config.CustomPricing{"ollama/llama3": {InputPerMTok: 2}}), 0.002},
	} {
		t.Run(tc.name, func(t *testing.T) {
			log := captureSlog(t)
			tracker := testTracker()
			p := New(tracker, tc.pricing, true, nil)
			rc := rcWithUsage("ollama/llama3", 1000, 500)
			rc.Metadata["cost_source"] = "catalogue"
			p.ProcessResponse(context.Background(), rc)
			if got, _ := tracker.GetSpend("org", ""); got != tc.want {
				t.Errorf("org spend = %v, want %v", got, tc.want)
			}
			if remaining := tracker.CheckBudget("", "", "", rc.Model, nil).Remaining; remaining != 10000-tc.want {
				t.Errorf("remaining = %v, want %v", remaining, 10000-tc.want)
			}
			if strings.Contains(log.String(), "budget: no pricing") {
				t.Errorf("known free model produced a missing-price warning: %s", log)
			}
		})
	}
}
