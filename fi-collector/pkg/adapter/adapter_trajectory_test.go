package adapter

import (
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestSplitOverflowPrefixesAndStringCutoff(t *testing.T) {
	m := pcommon.NewMap()
	m.PutStr("llm.input_messages.0.message.content", "Hello world")
	m.PutStr("llm.output_messages.0.message.content", "Hi there")
	m.PutStr("llm.tools.0.tool.json_schema", `{"name":"search"}`)
	m.PutStr("gen_ai.prompt.0.content", "Prompt text")
	m.PutStr("gen_ai.completion.0.content", "Completion text")
	m.PutStr("gen_ai.input.messages", `[{"role":"user","content":"hi"}]`)
	m.PutStr("gen_ai.output.messages", `[{"role":"assistant","content":"yo"}]`)
	m.PutStr("gen_ai.system_instructions", "System")
	m.PutStr("gen_ai.tool.definitions", `[]`)
	m.PutStr("raw.input", "raw dump")

	// Small non-overflow string stays in attrs_string; > 4096B moves to overflow.
	exactLimit := strings.Repeat("a", MaxAttrsStringBytes)
	overLimit := strings.Repeat("b", MaxAttrsStringBytes+1)
	m.PutStr("custom.exact", exactLimit)
	m.PutStr("custom.large", overLimit)
	m.PutStr("gen_ai.prompt", "short prompt stays in attrs_string")

	as := map[string]string{}
	an := map[string]float64{}
	ab := map[string]uint8{}
	of := map[string]any{}

	Split(m, as, an, ab, of)

	for _, k := range []string{
		"llm.input_messages.0.message.content",
		"llm.output_messages.0.message.content",
		"llm.tools.0.tool.json_schema",
		"gen_ai.prompt.0.content",
		"gen_ai.completion.0.content",
		"gen_ai.input.messages",
		"gen_ai.output.messages",
		"gen_ai.system_instructions",
		"gen_ai.tool.definitions",
		"raw.input",
		"custom.large",
	} {
		if _, inStr := as[k]; inStr {
			t.Errorf("key %q landed in attrs_string; want overflow", k)
		}
		if _, inOf := of[k]; !inOf {
			t.Errorf("key %q missing from overflow", k)
		}
	}

	if as["custom.exact"] != exactLimit {
		t.Errorf("custom.exact (%dB) should remain in attrs_string", len(exactLimit))
	}
	if _, inOf := of["custom.exact"]; inOf {
		t.Errorf("custom.exact should not be in overflow")
	}
	if as["gen_ai.prompt"] != "short prompt stays in attrs_string" {
		t.Errorf("short bare gen_ai.prompt should stay in attrs_string")
	}
}
