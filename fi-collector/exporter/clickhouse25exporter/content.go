package clickhouse25exporter

import (
	"encoding/json"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// Explicit content aliases precede native GenAI content. Presence is decisive:
// an empty/null/redacted alias must not expose a lower-priority raw attribute.
// Keep pkg/server's classifyKey in sync: every hot-column copy needs a budget.
var (
	inputContentKeys  = []string{"input.value", "fi.llm.input", "raw.input", "span.input", "gen_ai.prompt"}
	outputContentKeys = []string{"output.value", "fi.llm.output", "raw.output", "span.output", "gen_ai.completion"}
)

func spanContent(attrs pcommon.Map, observationType string) (string, string) {
	input, hasInput := contentValue(attrs, inputContentKeys...)
	output, hasOutput := contentValue(attrs, outputContentKeys...)
	if !hasInput {
		input, hasInput = contentValue(attrs, "gen_ai.input.messages")
		if observationType == "llm" {
			instructions, _ := contentValue(attrs, "gen_ai.system_instructions")
			input = withSystemInstructions(input, hasInput, instructions)
		}
	}
	if !hasOutput {
		output, hasOutput = contentValue(attrs, "gen_ai.output.messages")
	}
	if observationType == "tool" {
		if !hasInput {
			input, _ = contentValue(attrs, "gen_ai.tool.call.arguments")
		}
		if !hasOutput {
			output, _ = contentValue(attrs, "gen_ai.tool.call.result", "anthropic.tool_result.content")
		}
	}
	return input, output
}

// Read pdata directly, not the split numeric tier: tool arguments/results may
// be false, zero or int64 values that would lose precision through float64.
func contentValue(attrs pcommon.Map, keys ...string) (string, bool) {
	for _, key := range keys {
		if value, ok := attrs.Get(key); ok {
			if value.Type() == pcommon.ValueTypeStr {
				return value.Str(), true
			}
			if value.Type() == pcommon.ValueTypeEmpty {
				return "", true
			}
			data, err := json.Marshal(value.AsRaw())
			if err != nil {
				return "", true
			}
			return string(data), true
		}
	}
	return "", false
}

// Preserve the native message/part schema, including tool calls and image type
// markers. Splice validated arrays rather than unmarshal opaque JSON into an
// object graph: large prompts stay linear in bytes, and large numbers survive.
func withSystemInstructions(input string, hasInput bool, instructions string) string {
	parts, valid := jsonArrayBody(instructions)
	if !valid || parts == "" {
		return input
	}
	messages := ""
	if hasInput {
		var valid bool
		messages, valid = jsonArrayBody(input)
		if !valid {
			// A malformed or redacted message value remains authoritative.
			return input
		}
	}
	var out strings.Builder
	out.Grow(len(instructions) + len(input) + 32)
	out.WriteString(`[{"role":"system","parts":[`)
	out.WriteString(parts)
	out.WriteString(`]}`)
	if messages != "" {
		out.WriteByte(',')
		out.WriteString(messages)
	}
	out.WriteByte(']')
	return out.String()
}

func jsonArrayBody(value string) (string, bool) {
	value = strings.TrimSpace(value)
	if len(value) < 2 || value[0] != '[' || value[len(value)-1] != ']' || !json.Valid([]byte(value)) {
		return "", false
	}
	return strings.TrimSpace(value[1 : len(value)-1]), true
}
