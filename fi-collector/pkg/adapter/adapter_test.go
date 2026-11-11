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

func TestFirstNumber_PriorityAndStringCoercion(t *testing.T) {
	num := map[string]float64{"gen_ai.usage.input_tokens": 200}
	str := map[string]string{"llm.token_count.prompt": "100"}
	// llm.token_count.prompt has HIGHER priority than gen_ai.usage.input_tokens
	// and must win even though it arrived as a string (Django int()-coerced).
	v, ok := firstNumber(str, num, inputTokenKeys)
	if !ok || v != 100 {
		t.Fatalf("want 100 from string-typed higher-priority key, got %v ok=%v", v, ok)
	}

	// Number-typed value found when no higher-priority key present.
	v, ok = firstNumber(map[string]string{}, num, inputTokenKeys)
	if !ok || v != 200 {
		t.Fatalf("want 200, got %v ok=%v", v, ok)
	}

	// Unparseable string is skipped, falls through to next alias.
	str = map[string]string{"llm.token_count.prompt": "abc"}
	v, ok = firstNumber(str, num, inputTokenKeys)
	if !ok || v != 200 {
		t.Fatalf("want fallthrough to 200 past garbage string, got %v ok=%v", v, ok)
	}

	// Nothing present → not ok.
	if _, ok := firstNumber(map[string]string{}, map[string]float64{}, inputTokenKeys); ok {
		t.Fatal("want ok=false when no alias present")
	}
}

func TestDeriveHotKeys_AliasPositionsTableDriven(t *testing.T) {
	cases := []struct {
		name      string
		attrs     map[string]string
		wantModel string
		wantProv  string
	}{
		{
			name:      "llm.system alias position",
			attrs:     map[string]string{"llm.system": "openai"},
			wantModel: "",
			wantProv:  "openai",
		},
		{
			name:      "llm.request.model alias position (lowest priority)",
			attrs:     map[string]string{"llm.request.model": "gpt-3.5-turbo"},
			wantModel: "gpt-3.5-turbo",
			wantProv:  "",
		},
		{
			name: "full chain: all model aliases present → llm.model_name wins",
			attrs: map[string]string{
				"llm.model_name":        "fi-model",
				"gen_ai.request.model":  "gpt-4o",
				"gen_ai.response.model": "gpt-4o-2024-08-06",
				"llm.request.model":     "gpt-3.5-turbo",
			},
			wantModel: "fi-model",
			wantProv:  "",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			hk := DeriveHotKeys(c.attrs, nil)
			if hk.Model != c.wantModel {
				t.Errorf("Model: got %q want %q", hk.Model, c.wantModel)
			}
			if hk.Provider != c.wantProv {
				t.Errorf("Provider: got %q want %q", hk.Provider, c.wantProv)
			}
		})
	}
}

func TestFirstString_EmptyValueSkipsToNextAlias(t *testing.T) {
	// A present-but-empty first alias must be treated as absent and fall
	// through to the next alias in priority order.
	str := map[string]string{
		"llm.model_name":       "",
		"gen_ai.request.model": "gpt-4o",
	}
	if got := firstString(str, modelNameKeys); got != "gpt-4o" {
		t.Fatalf("want fallthrough to gpt-4o past empty first alias, got %q", got)
	}
}

func TestFirstString_Priority(t *testing.T) {
	str := map[string]string{
		"gen_ai.request.model": "gpt-4o",
		"llm.model_name":       "openai/gpt-4o", // FI convention — higher priority
	}
	if got := firstString(str, modelNameKeys); got != "openai/gpt-4o" {
		t.Fatalf("want llm.model_name to win, got %q", got)
	}
	if got := firstString(map[string]string{}, modelNameKeys); got != "" {
		t.Fatalf("want empty when absent, got %q", got)
	}
}

func TestDeriveHotKeys_TokensOpenInference(t *testing.T) {
	hk := DeriveHotKeys(
		map[string]string{},
		map[string]float64{
			"llm.token_count.prompt":     11,
			"llm.token_count.completion": 22,
			"llm.token_count.total":      33,
		},
	)
	if hk.PromptTokens != 11 || hk.CompletionTokens != 22 || hk.TotalTokens != 33 {
		t.Fatalf("OpenInference tokens not promoted: %+v", hk)
	}
}

func TestDeriveHotKeys_TokensOpenLLMetry(t *testing.T) {
	hk := DeriveHotKeys(
		map[string]string{},
		map[string]float64{
			"llm.usage.prompt_tokens":     5,
			"llm.usage.completion_tokens": 7,
		},
	)
	if hk.PromptTokens != 5 || hk.CompletionTokens != 7 {
		t.Fatalf("OpenLLMetry tokens not promoted: %+v", hk)
	}
	if hk.TotalTokens != 12 {
		t.Fatalf("total should be derived from parts, got %d", hk.TotalTokens)
	}
}

func TestDeriveHotKeys_TokensStringTyped(t *testing.T) {
	hk := DeriveHotKeys(
		map[string]string{"gen_ai.usage.input_tokens": "42"},
		map[string]float64{},
	)
	if hk.PromptTokens != 42 {
		t.Fatalf("string-typed tokens must coerce, got %d", hk.PromptTokens)
	}
}

func TestDeriveHotKeys_ModelAliases(t *testing.T) {
	// gen_ai.response.model alone must resolve (resolved names like gpt-4o-2024-08-06).
	hk := DeriveHotKeys(map[string]string{"gen_ai.response.model": "gpt-4o-2024-08-06"}, nil)
	if hk.Model != "gpt-4o-2024-08-06" {
		t.Fatalf("response.model not promoted, got %q", hk.Model)
	}
	// Django priority: llm.model_name beats gen_ai.request.model.
	hk = DeriveHotKeys(map[string]string{
		"llm.model_name":       "fi-model",
		"gen_ai.request.model": "gpt-4o",
	}, nil)
	if hk.Model != "fi-model" {
		t.Fatalf("llm.model_name must win (Django parity), got %q", hk.Model)
	}
}

func TestDeriveHotKeys_ProviderAliases(t *testing.T) {
	// gen_ai.provider.name is the current canonical key and must win.
	hk := DeriveHotKeys(map[string]string{
		"gen_ai.provider.name": "anthropic",
		"gen_ai.system":        "openai",
	}, nil)
	if hk.Provider != "anthropic" {
		t.Fatalf("gen_ai.provider.name must win, got %q", hk.Provider)
	}
	// GenAISystem column still mirrors raw gen_ai.system.
	if hk.GenAISystem != "openai" {
		t.Fatalf("GenAISystem must stay raw gen_ai.system, got %q", hk.GenAISystem)
	}
	// llm.vendor (OpenLLMetry) resolves when nothing else present.
	hk = DeriveHotKeys(map[string]string{"llm.vendor": "cohere"}, nil)
	if hk.Provider != "cohere" {
		t.Fatalf("llm.vendor not promoted, got %q", hk.Provider)
	}
}

func TestDeriveHotKeys_UserCost(t *testing.T) {
	// cost.total wins outright.
	hk := DeriveHotKeys(nil, map[string]float64{
		"gen_ai.cost.total": 0.5,
		"gen_ai.cost.input": 9.9, // must be ignored when total present
	})
	if !hk.CostUserSet || hk.Cost != 0.5 {
		t.Fatalf("cost.total must win: %+v", hk)
	}

	// input+output summed when no total.
	hk = DeriveHotKeys(nil, map[string]float64{
		"gen_ai.cost.input":  0.01,
		"gen_ai.cost.output": 0.02,
	})
	if !hk.CostUserSet || hk.Cost < 0.0299 || hk.Cost > 0.0301 {
		t.Fatalf("want input+output=0.03: %+v", hk)
	}

	// OpenInference llm.cost.* aliases work too.
	hk = DeriveHotKeys(nil, map[string]float64{"llm.cost.total": 1.25})
	if !hk.CostUserSet || hk.Cost != 1.25 {
		t.Fatalf("llm.cost.total not promoted: %+v", hk)
	}

	// Only one side present still counts as user-set (Django summed with `or 0`).
	hk = DeriveHotKeys(nil, map[string]float64{"llm.cost.prompt": 0.004})
	if !hk.CostUserSet || hk.Cost != 0.004 {
		t.Fatalf("single-sided user cost: %+v", hk)
	}

	// Explicit user zero is STILL user-set (blocks token-pricing fallback).
	hk = DeriveHotKeys(nil, map[string]float64{"gen_ai.cost.total": 0})
	if !hk.CostUserSet || hk.Cost != 0 {
		t.Fatalf("explicit zero must set CostUserSet: %+v", hk)
	}

	// No cost attrs → not user-set.
	hk = DeriveHotKeys(nil, map[string]float64{"gen_ai.usage.input_tokens": 10})
	if hk.CostUserSet {
		t.Fatalf("CostUserSet must be false without cost attrs")
	}
}
