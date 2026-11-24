package budget

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

func TestTokenPricingWarnings(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	billing := cost.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, endpoint := range []string{"chat", "completion", "responses"} {
		for _, tc := range []struct {
			model, source string
			warnings      int
		}{
			{"ollama/llama3", "catalogue", 0},
			{"fixture-unknown", "unpriced", 1},
		} {
			t.Run(endpoint+"/"+tc.source, func(t *testing.T) {
				log := captureSlog(t)
				budget := New(testTracker(), NewPricingLookup(nil), true, nil)
				for i := 0; i < 2; i++ {
					rc := rcWithUsage(tc.model, 1000, 500)
					rc.Provider, rc.EndpointType = "ollama", endpoint
					billing.ProcessResponse(context.Background(), rc)
					if _, hasCost := rc.Metadata["cost"]; hasCost || rc.Metadata["cost_source"] != tc.source {
						t.Fatalf("cost present=%t, source=%q; want absent, %q", hasCost, rc.Metadata["cost_source"], tc.source)
					}
					budget.ProcessResponse(context.Background(), rc)
				}
				if got := strings.Count(log.String(), "budget: no pricing for model"); got != tc.warnings {
					t.Errorf("budget warnings = %d, want %d", got, tc.warnings)
				}
			})
		}
	}
}

func TestTokenPricingWarningLimit(t *testing.T) {
	log := captureSlog(t)
	p := New(testTracker(), NewPricingLookup(nil), true, nil)
	for i := 0; i <= maxUnpricedWarnings; i++ {
		rc := rcWithUsage(fmt.Sprintf("fixture-%d", i), 1, 1)
		rc.EndpointType = []string{"chat", "completion", "responses"}[i%3]
		rc.Metadata["cost_source"] = "unpriced"
		p.ProcessResponse(context.Background(), rc)
	}
	if got := strings.Count(log.String(), "budget: no pricing for model"); got != 1024 {
		t.Errorf("distinct budget warnings = %d, want 1024", got)
	}
}
