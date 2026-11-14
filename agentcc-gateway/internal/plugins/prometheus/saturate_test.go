package prometheus

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A huge cost adds the saturated amount to the cost counter, which keeps
// growing: it never wraps negative on the next charge.
func TestPlugin_CostCounterAfterHugeCharges(t *testing.T) {
	reg := metrics.NewRegistry()
	p := New(reg, true)
	for _, cost := range []string{"0.000001", "10000000000000", "10000000000000"} {
		rc := newTestRC()
		rc.Metadata["cost"] = cost
		p.ProcessResponse(context.Background(), rc)
	}
	want := fmt.Sprintf(`agentcc_cost_microdollars_total{model="gpt-4-0613",provider="openai"} %d`, 1+2*models.MaxSpendMicros)
	if out := reg.Render(); !strings.Contains(out, want) {
		t.Fatalf("want %s in:\n%s", want, out)
	}
}
