package adapter

import (
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestSplitPreservesDevTypedRoutingByDefault(t *testing.T) {
	// Default Split must preserve dev routing so Error Feed, scalar attribute
	// filters (e.g. gen_ai.completion.0.finish_reason), and numeric raw.*
	// dashboards continue reading from attrs_string / attrs_number unchanged.
	m := pcommon.NewMap()
	m.PutStr("llm.input_messages.0.message.content", "Hello world")
	m.PutStr("llm.tools.0.tool.json_schema", `{"name":"search"}`)
	m.PutStr("gen_ai.completion.0.finish_reason", "length")
	m.PutDouble("raw.latency_ms", 42.5)
	m.PutStr("custom.large", strings.Repeat("b", 20_000))
	m.PutStr("input.value", `{"messages":[{"role":"user","content":"hi"}]}`)
	m.PutStr("output.value", `{"choices":[]}`)

	as := map[string]string{}
	an := map[string]float64{}
	ab := map[string]uint8{}
	of := map[string]any{}

	Split(m, as, an, ab, of)

	for _, k := range []string{
		"llm.input_messages.0.message.content",
		"llm.tools.0.tool.json_schema",
		"gen_ai.completion.0.finish_reason",
		"custom.large",
	} {
		if _, inStr := as[k]; !inStr {
			t.Errorf("key %q missing from attrs_string; default routing must stay dev-compatible", k)
		}
		if _, inOf := of[k]; inOf {
			t.Errorf("key %q unexpectedly moved to overflow", k)
		}
	}
	if an["raw.latency_ms"] != 42.5 {
		t.Errorf("raw.latency_ms = %v, want 42.5 in attrs_number", an["raw.latency_ms"])
	}
	if _, inOf := of["input.value"]; !inOf {
		t.Errorf("input.value must stay in overflow")
	}
	if _, inOf := of["output.value"]; !inOf {
		t.Errorf("output.value must stay in overflow")
	}
}
