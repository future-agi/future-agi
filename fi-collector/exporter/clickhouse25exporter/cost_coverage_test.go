package clickhouse25exporter

import (
	"context"
	"testing"
)

// A two-count pricer cannot price cache/reasoning units without separate rates.
// Keep observed tokens while exposing missing monetary coverage, not a guessed USD charge.
func TestQualifiedDetailedUsageCostCoverage(t *testing.T) {
	for _, key := range []string{"gen_ai.usage.cache_read_tokens", "gen_ai.usage.cache_write_tokens", "gen_ai.usage.output_tokens.reasoning"} {
		t.Run(key, func(t *testing.T) {
			td := copilotFixture(t)
			td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(1).Attributes().PutInt(key, 40)
			price := &normalizationPricer{}
			rows, _, err := ConvertWithIdentities(context.Background(), td, price)
			if err != nil {
				t.Fatal(err)
			}
			row := rows[1]
			if row["prompt_tokens"] != int32(14095) || row["cost"] != float64(0) || row["attrs_string"].(map[string]string)["fi.cost.kind"] != "unknown" {
				t.Fatalf("two-count estimate leaked into qualified detail: %v", row)
			}
			if price.calls != 3 {
				t.Fatalf("detail span must not call two-count pricer: %d", price.calls)
			}
		})
	}
}
