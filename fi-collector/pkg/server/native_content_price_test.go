package server

import (
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

// Every alias promoted into a hot content/session column must be priced before
// decode. Native content can be large even when it is one string attribute.
func TestScanCoversNativeContent(t *testing.T) {
	for _, key := range []string{
		"fi.llm.input", "raw.input", "span.input", "gen_ai.prompt", "gen_ai.input.messages", "gen_ai.system_instructions", "gen_ai.tool.call.arguments",
		"fi.llm.output", "raw.output", "span.output", "gen_ai.completion", "gen_ai.output.messages", "gen_ai.tool.call.result", "anthropic.tool_result.content",
		"gen_ai.conversation.id",
	} {
		for _, structured := range []bool{false, true} {
			td := calTraces(1, 1, 1, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
				attrs := sp.Attributes()
				attrs.PutStr("gen_ai.operation.name", "execute_tool")
				text := strings.Repeat("native \\\"<>&", 8192)
				if structured {
					attrs.PutEmptySlice(key).AppendEmpty().SetStr(text)
				} else {
					attrs.PutStr(key, text)
				}
				if key == "gen_ai.system_instructions" {
					attrs.PutStr("gen_ai.operation.name", "chat")
					attrs.PutStr("gen_ai.input.messages", `[{"role":"user","parts":[{"type":"text","content":"hello"}]}]`)
					attrs.PutEmptySlice(key).AppendEmpty().SetEmptyMap().PutStr("content", text)
				}
			})
			for _, jsonBody := range []bool{false, true} {
				t.Run(key+map[bool]string{false: "/scalar", true: "/structured"}[structured]+map[bool]string{false: "/proto", true: "/json"}[jsonBody], func(t *testing.T) {
					body := calBody(t, td, jsonBody)
					checkCovers(t, body, jsonBody)
					shape, err := scanBody(body, jsonBody)
					if err != nil {
						t.Fatal(err)
					}
					if key == "gen_ai.conversation.id" && !structured && shape.curatedJSONBytes < int64(len(strings.Repeat("native \\\"<>&", 8192))) {
						t.Error("conversation session not budgeted")
					}
				})
			}
		}
	}
}

func TestPriceCoversNativeSystemContent(t *testing.T) {
	for _, jsonBody := range calEncodings {
		td := calTraces(1, 1, 129, func(_ ptrace.ResourceSpans, sp ptrace.Span, _ int) {
			a := sp.Attributes()
			a.PutStr("gen_ai.operation.name", "chat")
			a.PutStr("gen_ai.system_instructions", `[{"type":"text","content":"`+strings.Repeat("instructions", 4096)+`"}]`)
			a.PutStr("gen_ai.input.messages", `[{"role":"user","parts":[{"type":"text","content":"`+strings.Repeat("prompt", 4096)+`"}]}]`)
		})
		body := calBody(t, td, jsonBody)
		shape, err := scanBody(body, jsonBody)
		if err != nil {
			t.Fatal(err)
		}
		checkCovers(t, body, jsonBody)
		handler, rows, _ := heldByRequest(t, body, jsonBody)
		p := shape.price()
		encodeShare := 2*shape.maxRowJSON + 2*shape.curatedJSONBytes
		if handler > p.handler || rows > p.rows-encodeShare {
			t.Errorf("native content heap exceeds admission: handler %d/%d, rows %d/%d", handler, p.handler, rows, p.rows-encodeShare)
		}
	}
}
