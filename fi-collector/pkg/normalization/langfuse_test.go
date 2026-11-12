package normalization

import (
	"reflect"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// langfuseAttrs builds span attributes from alternating keys and values.
func langfuseAttrs(kv ...any) map[string]any {
	m := make(map[string]any, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i].(string)] = kv[i+1]
	}
	return m
}

// Attribute names and value shapes are those the Langfuse Python SDK 5.2.0
// exports
// (https://github.com/langfuse/langfuse-python/blob/v5.2.0/langfuse/_client/attributes.py):
// input, output, usage_details and model.parameters are JSON strings, except
// that a str input or output is sent as is; each flattened metadata value is
// JSON-encoded, strings included; tags are a string array; the prompt version
// is an int; user.id and session.id are plain keys. "have" is the span as
// sent; "adds" is every key the mapping must add. Nothing else may change.
func TestLangfuseSpansMapToProductAttributes(t *testing.T) {
	question := `[{"role": "user", "content": "PARITY-LF What is the capital of France?"}]`
	answer := `{"role": "assistant", "content": "PARITY-LF The capital of France is Paris."}`
	for _, tc := range []struct {
		name string
		have map[string]any
		adds map[string]any
	}{
		{
			name: "generation",
			have: langfuseAttrs(
				"langfuse.observation.type", "generation",
				"langfuse.observation.model.name", "gpt-4o-mini",
				"langfuse.observation.model.parameters", `{"temperature": 0}`,
				"langfuse.observation.usage_details", `{"input": 12, "output": 9}`,
				"langfuse.observation.input", question,
				"langfuse.observation.output", answer,
				"langfuse.observation.prompt.name", "capital-qa",
				"langfuse.observation.prompt.version", 3,
				"langfuse.observation.metadata.team", `"search"`,
				"langfuse.observation.metadata.attempt", `2`,
				"langfuse.trace.tags", []any{"parity", "billing"},
				"user.id", "parity-user",
				"session.id", "parity-session",
			),
			adds: langfuseAttrs(
				"gen_ai.span.kind", "LLM",
				"llm.model_name", "gpt-4o-mini",
				"llm.provider", "openai",
				"llm.system", "openai",
				"llm.invocation_parameters", `{"temperature": 0}`,
				"llm.token_count.prompt", 12,
				"llm.token_count.completion", 9,
				"llm.token_count.total", 21,
				"input.value", question,
				"input.mime_type", "application/json",
				"output.value", answer,
				"output.mime_type", "application/json",
				"gen_ai.prompt.template.name", "capital-qa",
				"gen_ai.prompt.template.version", 3,
				"tag.tags", []any{"parity", "billing"},
				"metadata", `{"attempt":2,"team":"search"}`,
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "root span",
			have: langfuseAttrs(
				"langfuse.observation.type", "span",
				"langfuse.observation.input", `{"question": "PARITY-LF What is the capital of France?"}`,
				"langfuse.observation.output", `{"answer": "PARITY-LF The capital of France is Paris."}`,
				"langfuse.internal.is_app_root", true,
				"langfuse.trace.tags", []any{"parity"},
				"user.id", "parity-user",
				"session.id", "parity-session",
			),
			adds: langfuseAttrs(
				"gen_ai.span.kind", "CHAIN",
				"input.value", `{"question": "PARITY-LF What is the capital of France?"}`,
				"input.mime_type", "application/json",
				"output.value", `{"answer": "PARITY-LF The capital of France is Paris."}`,
				"output.mime_type", "application/json",
				"tag.tags", []any{"parity"},
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "openai integration usage and text content",
			have: langfuseAttrs(
				"langfuse.observation.type", "generation",
				"langfuse.observation.model.name", "gpt-4o-mini",
				"langfuse.observation.usage_details", `{"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "prompt_tokens_details": {"cached_tokens": 0}}`,
				"langfuse.observation.input", "What is 2 + 2?",
				"langfuse.observation.output", `"4"`,
			),
			adds: langfuseAttrs(
				"gen_ai.span.kind", "LLM",
				"llm.model_name", "gpt-4o-mini",
				"llm.provider", "openai",
				"llm.system", "openai",
				"llm.token_count.prompt", 20,
				"llm.token_count.completion", 5,
				"llm.token_count.total", 25,
				"input.value", "What is 2 + 2?",
				"input.mime_type", "text/plain",
				"output.value", "4",
				"output.mime_type", "text/plain",
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "embedding",
			have: langfuseAttrs(
				"langfuse.observation.type", "embedding",
				"langfuse.observation.model.name", "text-embedding-3-small",
				"langfuse.observation.usage_details", `{"input": 7}`,
				"langfuse.observation.input", "hello world",
			),
			adds: langfuseAttrs(
				"gen_ai.span.kind", "EMBEDDING",
				"llm.model_name", "text-embedding-3-small",
				"llm.token_count.prompt", 7,
				"input.value", "hello world",
				"input.mime_type", "text/plain",
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "event with langfuse user and session aliases",
			have: langfuseAttrs(
				"langfuse.observation.type", "event",
				"langfuse.user.id", "alias-user",
				"langfuse.session.id", "alias-session",
				"langfuse.observation.metadata", `{"source": "webhook"}`,
			),
			adds: langfuseAttrs(
				"gen_ai.span.kind", "UNKNOWN",
				"user.id", "alias-user",
				"session.id", "alias-session",
				"metadata", `{"source": "webhook"}`,
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "explicit gen_ai attributes win",
			have: langfuseAttrs(
				"langfuse.observation.type", "generation",
				"langfuse.observation.model.name", "gpt-4o-mini",
				"langfuse.observation.usage_details", `{"input": 12, "output": 9}`,
				"langfuse.observation.input", question,
				"langfuse.observation.output", answer,
				"langfuse.session.id", "alias-session",
				"gen_ai.span.kind", "AGENT",
				"gen_ai.request.model", "gpt-4.1-nano",
				"gen_ai.provider.name", "openai",
				"gen_ai.usage.input_tokens", 11,
				"input.value", "explicit input",
				"session.id", "explicit-session",
			),
			adds: langfuseAttrs(
				"output.value", answer,
				"output.mime_type", "application/json",
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "explicit fi.span.kind and empty input.value win",
			have: langfuseAttrs(
				"langfuse.observation.type", "generation",
				"langfuse.observation.model.name", "claude-sonnet-4-5",
				"langfuse.observation.input", question,
				"fi.span.kind", "TOOL",
				"input.value", "",
			),
			adds: langfuseAttrs(
				"llm.model_name", "claude-sonnet-4-5",
				"llm.provider", "anthropic",
				"llm.system", "anthropic",
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "explicit operation name and tool arguments win",
			have: langfuseAttrs(
				"langfuse.observation.type", "tool",
				"langfuse.observation.input", `{"city": "Paris"}`,
				"gen_ai.operation.name", "execute_tool",
				"gen_ai.tool.call.arguments", `{"city": "Paris"}`,
			),
			adds: langfuseAttrs(
				"gen_ai.trace.source", "langfuse",
			),
		},
		{
			name: "plain OTLP span unchanged",
			have: langfuseAttrs(
				"gen_ai.operation.name", "chat",
				"gen_ai.system", "openai",
				"gen_ai.provider.name", "openai",
				"gen_ai.request.model", "gpt-4.1-nano",
				"gen_ai.response.model", "gpt-4.1-nano",
				"gen_ai.request.temperature", 0.0,
				"gen_ai.usage.input_tokens", 11,
				"gen_ai.usage.output_tokens", 8,
				"gen_ai.input.messages", `[{"role": "user", "parts": [{"type": "text", "content": "PARITY-OTLP What is 2 + 2?"}]}]`,
				"gen_ai.output.messages", `[{"role": "assistant", "parts": [{"type": "text", "content": "PARITY-OTLP 2 + 2 is 4."}], "finish_reason": "stop"}]`,
			),
			adds: langfuseAttrs(),
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			attrs := pcommon.NewMap()
			if err := attrs.FromRaw(tc.have); err != nil {
				t.Fatal(err)
			}
			want := pcommon.NewMap()
			if err := want.FromRaw(tc.have); err != nil {
				t.Fatal(err)
			}
			for key, value := range tc.adds {
				if _, clash := tc.have[key]; clash {
					t.Fatalf("test adds %s, which the span already has", key)
				}
				if err := want.PutEmpty(key).FromRaw(value); err != nil {
					t.Fatal(err)
				}
			}
			Normalize(attrs, nil)
			if !reflect.DeepEqual(attrs.AsRaw(), want.AsRaw()) {
				t.Fatalf("got  %#v\nwant %#v", attrs.AsRaw(), want.AsRaw())
			}
			Normalize(attrs, nil)
			if !reflect.DeepEqual(attrs.AsRaw(), want.AsRaw()) {
				t.Fatal("Langfuse mapping is not idempotent")
			}
		})
	}
}
