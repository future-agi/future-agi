package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

func TestPositiveTierSurvivesFlatOverride(t *testing.T) {
	zero := 0.0
	for _, key := range []string{"dashscope/qwen-flash", "opencode_go/grok-4.7"} {
		t.Run(key, func(t *testing.T) {
			flat := &modeldb.PricingOverride{InputPerToken: &zero, OutputPerToken: &zero}
			if key == "opencode_go/grok-4.7" {
				flat.CachedInputPerToken = &zero
			}
			db := modeldb.New(modeldb.BundledModels, map[string]modeldb.ModelOverride{key: {Pricing: flat}})
			p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
			rc := newRC(key, 1000, 500)
			p.ProcessResponse(context.Background(), rc)
			if _, billed := rc.Metadata["cost"]; billed || rc.Metadata["cost_source"] != "unpriced" {
				t.Fatalf("want unpriced without cost; got %v", rc.Metadata)
			}
		})
	}
}
