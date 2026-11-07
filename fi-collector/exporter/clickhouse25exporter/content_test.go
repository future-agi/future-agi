package clickhouse25exporter

import (
	"encoding/json"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestNativeSystemInstructions(t *testing.T) {
	const system = `[{"type":"text","content":"system"}]`
	const messages = `[{"role":"user","parts":[{"type":"text","content":"prompt"},{"type":"tool_call","arguments":{"id":9007199254740993}}]}]`
	combined := `[{"role":"system","parts":` + system + `},` + messages[1:]
	for _, tc := range []struct {
		name, input, system string
		present             bool
		want                string
	}{
		{"both", messages, system, true, combined},
		{"system only", "", system, false, `[{"role":"system","parts":` + system + `}]`},
		{"empty messages", " [ ] ", system, true, `[{"role":"system","parts":` + system + `}]`},
		{"no system", messages, "", true, messages},
		{"truncated system", messages, " [ ] ", true, messages},
		{"redacted system", messages, "__REDACTED__", true, messages},
		{"malformed system", messages, "[", true, messages},
		{"object system", messages, `{}`, true, messages},
		{"redacted messages", "__REDACTED__", system, true, "__REDACTED__"},
		{"malformed messages", "[", system, true, "["},
		{"empty attribute", "", system, true, ""},
		{"null messages", "null", system, true, "null"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := withSystemInstructions(tc.input, tc.present, tc.system); got != tc.want {
				t.Errorf("got %s, want %s", got, tc.want)
			}
		})
	}
}

func TestNativeSystemStructuredAndAliasPrecedence(t *testing.T) {
	for _, alias := range []string{"input.value", "fi.llm.input", "raw.input", "span.input", "gen_ai.prompt"} {
		for _, value := range []any{"", nil, "__REDACTED__"} {
			t.Run(alias+"/"+fmtValue(value), func(t *testing.T) {
				row := convertOne(t, contentSpan(func(attrs pcommon.Map) {
					if err := attrs.FromRaw(map[string]any{
						"gen_ai.operation.name": "chat", alias: value,
						"gen_ai.system_instructions": []any{map[string]any{"type": "text", "content": "SYSTEM_SENTINEL"}},
						"gen_ai.input.messages":      []any{map[string]any{"role": "user", "parts": []any{}}},
					}); err != nil {
						t.Fatal(err)
					}
				}))
				if row["input"] != fmtValue(value) {
					t.Fatal("alias did not suppress system content", row["input"])
				}
			})
		}
	}
	row := convertOne(t, contentSpan(func(attrs pcommon.Map) {
		attrs.PutStr("gen_ai.operation.name", "chat")
		attrs.PutEmptySlice("gen_ai.system_instructions").AppendEmpty().SetEmptyMap().PutStr("content", "system")
		attrs.PutEmptySlice("gen_ai.input.messages").AppendEmpty().SetEmptyMap().PutStr("role", "user")
	}))
	var messages []map[string]any
	if err := json.Unmarshal([]byte(row["input"].(string)), &messages); err != nil || len(messages) != 2 || messages[0]["role"] != "system" {
		t.Fatal("structured native content lost", row["input"], err)
	}
}

func TestNativeContentRespectsExplicitKind(t *testing.T) {
	row := convertOne(t, contentSpan(func(attrs pcommon.Map) {
		attrs.PutStr("fi.span.kind", "chain")
		attrs.PutStr("gen_ai.operation.name", "execute_tool")
		attrs.PutStr("gen_ai.tool.call.arguments", "arguments")
		attrs.PutStr("anthropic.tool_result.content", "results")
		attrs.PutStr("gen_ai.system_instructions", `[{"content":"system"}]`)
	}))
	if row["observation_type"] != "chain" || row["input"] != "" || row["output"] != "" {
		t.Fatal("native operation overrode explicit span kind")
	}
}
