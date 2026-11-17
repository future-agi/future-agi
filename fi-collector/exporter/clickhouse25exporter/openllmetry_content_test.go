package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func assertOpenLLMetryJSON(t *testing.T, got, want string) {
	t.Helper()
	decode := func(s string) any {
		t.Helper()
		dec := json.NewDecoder(strings.NewReader(s))
		dec.UseNumber()
		var value any
		if err := dec.Decode(&value); err != nil {
			t.Fatalf("invalid message JSON %q: %v", s, err)
		}
		return value
	}
	if !reflect.DeepEqual(decode(got), decode(want)) {
		t.Errorf("messages = %s, want %s", got, want)
	}
}

func TestOpenLLMetryIndexedMessages(t *testing.T) {
	cases := []struct {
		name       string
		fill       func(pcommon.Map)
		wantInput  string
		wantOutput string
	}{
		{
			name: "roles_order_content",
			fill: func(m pcommon.Map) {
				// Insertion and lexical order both differ from message order.
				m.PutStr("gen_ai.prompt.10.role", "tool")
				m.PutStr("gen_ai.prompt.10.content", "result")
				m.PutStr("gen_ai.prompt.10.tool_call_id", "call_example")
				m.PutStr("gen_ai.prompt.2.role", "user")
				m.PutStr("gen_ai.prompt.2.content", "question")
				m.PutStr("gen_ai.prompt.0.role", "system")
				m.PutStr("gen_ai.prompt.0.content", "instructions")
				m.PutStr("gen_ai.completion.10.role", "assistant")
				m.PutStr("gen_ai.completion.10.content", "second choice")
				m.PutStr("gen_ai.completion.2.role", "assistant")
				m.PutStr("gen_ai.completion.2.content", "first choice")
			},
			wantInput:  `[{"role":"system","content":"instructions"},{"role":"user","content":"question"},{"role":"tool","content":"result","tool_call_id":"call_example"}]`,
			wantOutput: `[{"role":"assistant","content":"first choice"},{"role":"assistant","content":"second choice"}]`,
		},
		{
			name: "structured_content_and_tool_fields",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.prompt.0.role", "user")
				m.PutStr("gen_ai.prompt.0.content", `[{"type":"text","text":"question","number":9007199254740993}]`)
				m.PutStr("gen_ai.prompt.1.role", "assistant")
				call := m.PutEmptySlice("gen_ai.prompt.1.tool_calls").AppendEmpty().SetEmptyMap()
				call.PutStr("id", "call_example")
				fn := call.PutEmptyMap("function")
				fn.PutStr("name", "lookup")
				fn.PutStr("arguments", `{"query":"example"}`)
				m.PutStr("gen_ai.completion.0.role", "assistant")
				m.PutStr("gen_ai.completion.0.tool_calls.0.id", "call_next")
				m.PutStr("gen_ai.completion.0.tool_calls.0.function.name", "lookup")
				m.PutStr("gen_ai.completion.0.tool_calls.0.function.arguments", `{"query":"next"}`)
				m.PutBool("gen_ai.completion.0.parallel_tool_calls", false)
				m.PutInt("gen_ai.completion.0.index", 0)
				m.PutStr("gen_ai.completion.2.role", "assistant")
				part := m.PutEmptySlice("gen_ai.completion.2.content").AppendEmpty().SetEmptyMap()
				part.PutStr("type", "text")
				part.PutStr("text", "answer")
			},
			wantInput:  `[{"role":"user","content":[{"type":"text","text":"question","number":9007199254740993}]},{"role":"assistant","content":"","tool_calls":[{"id":"call_example","function":{"name":"lookup","arguments":"{\"query\":\"example\"}"}}]}]`,
			wantOutput: `[{"role":"assistant","content":"","tool_calls.0.id":"call_next","tool_calls.0.function.name":"lookup","tool_calls.0.function.arguments":"{\"query\":\"next\"}","parallel_tool_calls":false,"index":0},{"role":"assistant","content":[{"type":"text","text":"answer"}]}]`,
		},
		{
			name: "sparse_indices_and_missing_fields",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.prompt.1000000.content", "last")
				m.PutStr("gen_ai.prompt.2.content", "{not JSON")
				m.PutStr("gen_ai.completion.0.role", "assistant")
			},
			wantInput:  `[{"role":"user","content":"{not JSON"},{"role":"user","content":"last"}]`,
			wantOutput: `[{"role":"assistant","content":""}]`,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			td := contentSpan(func(m pcommon.Map) {
				m.PutStr("llm.request.type", "chat")
				c.fill(m)
			})
			attrs := td.ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
			before := attrs.AsRaw()
			rows, ids, err := ConvertWithIdentities(context.Background(), td, nil)
			if err != nil {
				t.Fatal(err)
			}
			if len(rows) != 1 {
				t.Fatalf("rows=%d, want 1", len(rows))
			}
			r := rows[0]
			if r["observation_type"] != "llm" {
				t.Errorf("observation_type = %v, want llm", r["observation_type"])
			}
			assertOpenLLMetryJSON(t, r["input"].(string), c.wantInput)
			assertOpenLLMetryJSON(t, r["output"].(string), c.wantOutput)
			traces := ids.Traces()
			if len(traces) != 1 {
				t.Fatalf("trace identities=%d, want 1", len(traces))
			}
			if traces[0].Input != r["input"] || traces[0].Output != r["output"] {
				t.Errorf("trace identity content differs from span content: %+v", traces[0])
			}
			for key, want := range before {
				got, ok := attrs.Get(key)
				if !ok || !reflect.DeepEqual(got.AsRaw(), want) {
					t.Errorf("source attribute %s changed", key)
				}
			}
		})
	}
}

func TestOpenLLMetryContentPrecedence(t *testing.T) {
	const indexedInput = `[{"content":"indexed prompt","role":"user"}]`
	const indexedOutput = `[{"content":"indexed reply","role":"assistant"}]`
	cases := []struct {
		name       string
		fill       func(pcommon.Map)
		wantInput  string
		wantOutput string
	}{
		{
			name: "explicit_values",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "explicit prompt")
				m.PutStr("output.value", "explicit reply")
			},
			wantInput: "explicit prompt", wantOutput: "explicit reply",
		},
		{
			name: "explicit_structured_values",
			fill: func(m pcommon.Map) {
				m.PutEmptyMap("input.value").PutStr("query", "explicit")
				m.PutEmptySlice("output.value").AppendEmpty().SetStr("explicit")
			},
			wantInput: `{"query":"explicit"}`, wantOutput: `["explicit"]`,
		},
		{
			name: "redacted_values",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "__REDACTED__")
				m.PutStr("output.value", "__REDACTED__")
			},
			wantInput: "__REDACTED__", wantOutput: "__REDACTED__",
		},
		{
			name: "explicit_empty_values",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "")
				m.PutEmpty("output.value")
			},
			wantInput: "", wantOutput: "",
		},
		{
			name: "span_aggregate_values",
			fill: func(m pcommon.Map) {
				m.PutStr("span.input", "aggregate prompt")
				m.PutStr("span.output", "aggregate reply")
			},
			wantInput: "aggregate prompt", wantOutput: "aggregate reply",
		},
		{
			name: "legacy_aggregate_values",
			fill: func(m pcommon.Map) {
				m.PutStr("gen_ai.prompt", "aggregate prompt")
				m.PutStr("gen_ai.completion", "aggregate reply")
			},
			wantInput: "aggregate prompt", wantOutput: "aggregate reply",
		},
		{
			name: "current_aggregate_values",
			fill: func(m pcommon.Map) {
				m.PutEmptySlice("gen_ai.input.messages").AppendEmpty().SetStr("aggregate prompt")
				m.PutStr("gen_ai.output.messages", `[{"role":"assistant","content":"aggregate reply"}]`)
			},
			wantInput: `["aggregate prompt"]`, wantOutput: `[{"role":"assistant","content":"aggregate reply"}]`,
		},
		{
			name: "input_precedence_output_fallback",
			fill: func(m pcommon.Map) {
				m.PutStr("input.value", "explicit prompt")
			},
			wantInput: "explicit prompt", wantOutput: indexedOutput,
		},
		{
			name: "output_precedence_input_fallback",
			fill: func(m pcommon.Map) {
				m.PutStr("output.value", "explicit reply")
			},
			wantInput: indexedInput, wantOutput: "explicit reply",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := convertOne(t, contentSpan(func(m pcommon.Map) {
				m.PutStr("llm.request.type", "chat")
				m.PutStr("gen_ai.prompt.0.role", "user")
				m.PutStr("gen_ai.prompt.0.content", "indexed prompt")
				m.PutStr("gen_ai.completion.0.role", "assistant")
				m.PutStr("gen_ai.completion.0.content", "indexed reply")
				c.fill(m)
			}))
			if r["input"] != c.wantInput || r["output"] != c.wantOutput {
				t.Errorf("input/output = %q/%q, want %q/%q", r["input"], r["output"], c.wantInput, c.wantOutput)
			}
			if r["attrs_string"].(map[string]string)["gen_ai.prompt.0.content"] != "indexed prompt" {
				t.Error("original indexed prompt attribute was lost")
			}
		})
	}
}

func TestOpenLLMetryMalformedIndexedKeys(t *testing.T) {
	r := convertOne(t, contentSpan(func(m pcommon.Map) {
		for _, prefix := range []string{"gen_ai.prompt", "gen_ai.completion"} {
			for _, suffix := range []string{".word.content", ".-1.content", ".+1.content", "..content", ".0", ".0.", ".999999999999999999999999999999.content"} {
				m.PutStr(prefix+suffix, "not a message")
			}
		}
		m.PutStr("gen_ai.prompts.0.content", "not a message")
	}))
	if r["input"] != "" || r["output"] != "" {
		t.Errorf("malformed keys produced input/output = %q/%q", r["input"], r["output"])
	}
}
