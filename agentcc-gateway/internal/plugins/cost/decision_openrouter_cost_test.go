package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// Jev called through OpenRouter is priced at the vendor rate under the names
// OpenRouter uses: the caller's, the upstream's and the dated one it answers
// with. An unknown Jev version stays unpriced.
func TestCostPlugin_DecisionOpenRouterJev(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	for _, tt := range []struct {
		name, model, upstream, resolved string
	}{
		{"jev-1.13 dated", "openrouter/typesafe/jev-1.13", "typesafe/jev-1.13", "typesafe/jev-1.13-20260917"},
		{"jev-1.14 dated", "openrouter/typesafe/jev-1.14", "typesafe/jev-1.14", "typesafe/jev-1.14-20261015"},
		{"jev-latest", "openrouter/~typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.14-20261015"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			rc := decisionRC(tt.model, tt.upstream, tt.resolved, 1_000_000, 5)
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != "0.042000" {
				t.Fatalf("cost = %q, want 0.042000", got)
			}
			if src, ok := rc.Metadata["cost_source"]; ok {
				t.Fatalf("cost_source = %q, want none", src)
			}
		})
	}

	t.Run("unknown version", func(t *testing.T) {
		rc := decisionRC("openrouter/typesafe/jev-9.9", "typesafe/jev-9.9", "typesafe/jev-9.9-20270101", 1_000_000, 5)
		p.ProcessResponse(context.Background(), rc)
		if got, ok := rc.Metadata["cost"]; ok {
			t.Fatalf("unknown Jev version got cost %q", got)
		}
		if got := rc.Metadata["cost_source"]; got != "unpriced" {
			t.Fatalf("cost_source = %q, want unpriced", got)
		}
	})
}
