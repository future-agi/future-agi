package normalization

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

func TestSharedCostCoverageVectors(t *testing.T) {
	raw, err := os.ReadFile("../../../futureagi/tracer/utils/normalization/cost_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Name       string
		Attributes map[string]any
		Held       bool
	}
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors {
		for _, usageKey := range registry.UnpricedUsageKeys {
			t.Run(v.Name+"/"+usageKey, func(t *testing.T) {
				td := ptrace.NewTraces()
				ss := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
				ss.Scope().SetName("github.copilot")
				ss.Scope().SetVersion("1.0.98")
				attrs := ss.Spans().AppendEmpty().Attributes()
				if err := attrs.FromRaw(v.Attributes); err != nil {
					t.Fatal(err)
				}
				attrs.PutInt(usageKey, 40)
				before := attrs.AsRaw()
				want := ""
				if v.Held {
					want = CostCoverageError()
				}
				if reason := IngestionHold(td); reason != want {
					t.Fatalf("got %q, want %q", reason, want)
				}
				if !reflect.DeepEqual(attrs.AsRaw(), before) {
					t.Fatal("admission mutated the unbudgeted request")
				}
				ss.Scope().SetVersion("unqualified")
				if IngestionHold(td) != "" {
					t.Fatal("changed unqualified producer behavior")
				}
				ss.Scope().SetVersion("1.0.98")
				Prepare(td)
				if IngestionHold(td) != want {
					t.Fatal("prepared/direct conversion bypassed hold")
				}
			})
		}
	}
}
