package clickhouse25exporter

import (
	"os"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

// The fixture preserves native IDs, scope, parents, names, timestamps and usage;
// resource identity, prompts, arguments, outputs and private attributes are removed.
func TestCapturedVSCodeCopilotUsageAndTiming(t *testing.T) {
	raw, err := os.ReadFile("../../../futureagi/tracer/utils/normalization/vscode-copilot-1.0.100-1.json")
	if err != nil {
		t.Fatal(err)
	}
	td, err := (&ptrace.JSONUnmarshaler{}).UnmarshalTraces(raw)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := Convert(td)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 91 {
		t.Fatalf("spans = %d, want 91", len(rows))
	}
	var prompt, completion, total int64
	llms, timing, agents := 0, 0, 0
	for _, row := range rows {
		// JSON row conversion represents token counts as int32.
		prompt += int64(row["prompt_tokens"].(int32))
		completion += int64(row["completion_tokens"].(int32))
		total += int64(row["total_tokens"].(int32))
		switch row["observation_type"] {
		case "llm":
			llms++
		case "internal":
			timing++
		case "agent":
			agents++
		}
	}
	if prompt != 87533 || completion != 540 || total != 88073 || llms != 5 || timing != 72 || agents != 1 {
		t.Fatalf("got input=%d output=%d total=%d llms=%d timing=%d agents=%d", prompt, completion, total, llms, timing, agents)
	}
}
