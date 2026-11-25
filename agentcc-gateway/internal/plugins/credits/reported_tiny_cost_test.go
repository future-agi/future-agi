package credits

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// A non-zero reported cost always deducts at least one micro-dollar; exactly 0
// deducts nothing; larger costs round to the nearest micro-dollar as before.
func TestCreditsTinyReportedCostDeductsAtLeastOneMicro(t *testing.T) {
	for _, tc := range []struct {
		ticks int64 // xAI: 1e10 ticks per USD
		want  int64
	}{
		{4_000, 1},  // $0.0000004
		{0, 0},      // a stated $0
		{14_000, 1}, // $0.0000014 rounds to 1, as before
		{15_000, 2}, // $0.0000015 rounds to 2, as before
	} {
		t.Run(fmt.Sprint(tc.ticks), func(t *testing.T) {
			ks := newKeyStore()
			managed := findKey(ks, "managed")
			before := managed.BalanceMicros()
			db := modeldb.New(modeldb.BundledModels, nil)
			cost := costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil)
			cost.SetReportedCostVendors(func(*models.RequestContext) string { return "xai" })
			rc := makeRC(managed.ID, "managed")
			defer rc.Release()
			rc.Provider, rc.Model = "xai", "grok-3"
			var usage models.Usage
			if err := json.Unmarshal([]byte(fmt.Sprintf(`{"prompt_tokens":10,"completion_tokens":0,"cost_in_usd_ticks":%d}`, tc.ticks)), &usage); err != nil {
				t.Fatal(err)
			}
			rc.Response = &models.ChatCompletionResponse{Usage: &usage}
			cost.ProcessResponse(context.Background(), rc)
			New(true, ks).ProcessResponse(context.Background(), rc)
			if got := before - managed.BalanceMicros(); got != tc.want {
				t.Errorf("deducted %d micros for cost %q, want %d", got, rc.Metadata["cost"], tc.want)
			}
		})
	}
}
