package adapter

import (
	"math"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// buildAttrs constructs a pcommon.Map covering every routing rule:
//   - plain string → attrs_string
//   - bool → attrs_bool
//   - safe int → attrs_number
//   - unsafe int (>2^53) → overflow
//   - double finite → attrs_number
//   - double NaN/Inf → overflow
//   - slice / map → overflow
//   - overflow-prefix key → overflow regardless of type
func buildAttrs() pcommon.Map {
	m := pcommon.NewMap()
	m.PutStr("user.name", "alice")
	m.PutBool("user.is_premium", true)
	m.PutInt("retry.count", 3)
	m.PutInt("legacy.bigint", int64(maxSafeInt+1))
	m.PutDouble("latency.ms", 12.5)
	m.PutDouble("ratio.nan", math.NaN())
	m.PutDouble("ratio.inf", math.Inf(+1))
	m.PutEmptySlice("models").AppendEmpty().SetStr("gpt-4o")
	m.PutEmptyMap("nested").PutStr("k", "v")
	m.PutStr("llm.prompt.0.role", "user")      // overflow prefix
	m.PutStr("input.value", "Hi")              // overflow prefix
	return m
}

func TestSplit(t *testing.T) {
	a := buildAttrs()
	str := map[string]string{}
	num := map[string]float64{}
	bl := map[string]uint8{}
	of := map[string]any{}
	Split(a, str, num, bl, of)

	cases := []struct {
		name string
		ok   bool
	}{
		{"attrs_string user.name", str["user.name"] == "alice"},
		{"attrs_bool user.is_premium", bl["user.is_premium"] == 1},
		{"attrs_number retry.count", num["retry.count"] == 3},
		{"attrs_number latency.ms", num["latency.ms"] == 12.5},
	}
	for _, c := range cases {
		if !c.ok {
			t.Errorf("split rule failed: %s", c.name)
		}
	}

	if _, ok := num["legacy.bigint"]; ok {
		t.Errorf("bigint should overflow (loses precision in Float64)")
	}
	if _, ok := of["legacy.bigint"]; !ok {
		t.Errorf("bigint missing from overflow")
	}
	if _, ok := of["ratio.nan"]; !ok {
		t.Errorf("NaN should overflow")
	}
	if _, ok := of["ratio.inf"]; !ok {
		t.Errorf("Inf should overflow")
	}
	if _, ok := of["models"]; !ok {
		t.Errorf("slice should overflow")
	}
	if _, ok := of["nested"]; !ok {
		t.Errorf("nested map should overflow")
	}
	if _, ok := of["llm.prompt.0.role"]; !ok {
		t.Errorf("llm.prompt.* must overflow regardless of scalar type")
	}
	if _, ok := str["llm.prompt.0.role"]; ok {
		t.Errorf("llm.prompt.* must NOT land in attrs_string")
	}
	if _, ok := of["input.value"]; !ok {
		t.Errorf("input.value must overflow (caller reads from overflow to fill `input`)")
	}
}

func TestOverflowToJSON(t *testing.T) {
	if got := OverflowToJSON(map[string]any{}); got != "{}" {
		t.Errorf("empty got %q want {}", got)
	}
	got := OverflowToJSON(map[string]any{"k": "v"})
	if got != `{"k":"v"}` {
		t.Errorf("simple got %q", got)
	}
}

func TestDeriveHotKeys_GenAICanonical(t *testing.T) {
	hk := DeriveHotKeys(
		map[string]string{
			"gen_ai.request.model":  "gpt-4o",
			"gen_ai.system":         "openai",
			"gen_ai.operation.name": "chat",
		},
		map[string]float64{
			"gen_ai.usage.input_tokens":  120,
			"gen_ai.usage.output_tokens": 38,
			"gen_ai.usage.total_tokens":  158,
		},
	)
	if hk.Model != "gpt-4o" || hk.Provider != "openai" {
		t.Errorf("model/provider: %+v", hk)
	}
	if hk.PromptTokens != 120 || hk.TotalTokens != 158 {
		t.Errorf("tokens: %+v", hk)
	}
}

func TestDeriveHotKeys_LegacyFallback(t *testing.T) {
	hk := DeriveHotKeys(
		map[string]string{
			"llm.model_name": "claude-3-5",
			"llm.provider":   "anthropic",
		},
		map[string]float64{
			"gen_ai.usage.input_tokens":  10,
			"gen_ai.usage.output_tokens": 20,
		},
	)
	if hk.Model != "claude-3-5" || hk.Provider != "anthropic" {
		t.Errorf("legacy fallback: %+v", hk)
	}
	// Total derived from parts when not explicitly provided.
	if hk.TotalTokens != 30 {
		t.Errorf("total derived: got %d want 30", hk.TotalTokens)
	}
}
