package normalization

import (
	"bytes"
	"encoding/json"
	"strconv"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// Langfuse SDK spans (Python v3 and later, 5.2.0 included) describe the
// observation only in the langfuse.* namespace, plus plain user.id and
// session.id:
// https://github.com/langfuse/langfuse-python/blob/v5.2.0/langfuse/_client/attributes.py
// The converter reads none of those keys, so a Langfuse trace sent to the
// OTel path showed Type unknown, 0 tokens, $0.00 and no input or output.
//
// normalizeLangfuse writes the keys the converter reads for every other
// source, under the names futureagi/tracer/utils/adapters/langfuse.py writes
// (that adapter runs only on the deprecated /api/public/ingestion route).
// Each value is written only when every key the converter reads for it is
// absent, so an explicit OTel attribute always wins and a second pass changes
// nothing. The raw langfuse.* attributes are kept.
func normalizeLangfuse(attrs pcommon.Map) {
	if !isLangfuse(attrs) {
		return
	}
	model := String(attrs, "langfuse.observation.model.name")
	// gen_ai.operation.name is the converter's kind fallback, so it is
	// explicit too.
	if !has(attrs, kindKeys...) && !has(attrs, "gen_ai.operation.name") {
		if kind := langfuseKind(strings.ToLower(String(attrs, "langfuse.observation.type")), model); kind != "" {
			attrs.PutStr("gen_ai.span.kind", kind)
		}
	}
	// The adapter maps model and usage on LLM spans only. SDK 5.2.0 sets them
	// on generation and embedding observations only, so they are mapped
	// wherever present and an embedding gets its tokens and cost too.
	if model != "" && !has(attrs, langfuseModelReaders...) {
		attrs.PutStr("llm.model_name", model)
	}
	if provider := langfuseProvider(model); provider != "" && !has(attrs, langfuseProviderReaders...) {
		attrs.PutStr("llm.provider", provider)
		attrs.PutStr("llm.system", provider)
	}
	copyAbsent(attrs, "langfuse.observation.model.parameters", "llm.invocation_parameters")
	if !has(attrs, langfuseTokenReaders...) {
		putLangfuseUsage(attrs, String(attrs, "langfuse.observation.usage_details"))
	}
	putLangfuseContent(attrs, "langfuse.observation.input", "input", langfuseInputReaders)
	putLangfuseContent(attrs, "langfuse.observation.output", "output", langfuseOutputReaders)
	// SDK 5.2.0 already sends user.id and session.id; the langfuse.* names
	// are the aliases the Langfuse server also accepts.
	copyAbsent(attrs, "langfuse.user.id", "user.id")
	copyAbsent(attrs, "langfuse.session.id", "session.id")
	copyAbsent(attrs, "langfuse.trace.tags", "tag.tags")
	copyAbsent(attrs, "langfuse.observation.prompt.name", "gen_ai.prompt.template.name")
	copyAbsent(attrs, "langfuse.observation.prompt.version", "gen_ai.prompt.template.version")
	if !has(attrs, "metadata") {
		putLangfuseMetadata(attrs)
	}
	if !has(attrs, "gen_ai.trace.source") {
		attrs.PutStr("gen_ai.trace.source", "langfuse")
	}
}

// The keys the converter reads for each value, in its priority lists
// (pkg/adapter/adapter.go modelNameKeys, providerKeys, the token keys, and
// clickhouse25exporter/converter.go inputContentKeys / outputContentKeys).
var langfuseModelReaders = []string{"llm.model_name", "gen_ai.request.model", "gen_ai.response.model", "llm.request.model"}

var langfuseProviderReaders = []string{"gen_ai.provider.name", "llm.system", "gen_ai.system", "llm.vendor", "llm.provider"}

var langfuseTokenReaders = []string{
	"llm.token_count.prompt", "gen_ai.usage.input_tokens", "llm.usage.prompt_tokens", "gen_ai.usage.prompt_tokens",
	"llm.token_count.completion", "gen_ai.usage.output_tokens", "llm.usage.completion_tokens", "gen_ai.usage.completion_tokens",
	"llm.token_count.total", "gen_ai.usage.total_tokens", "llm.usage.total_tokens",
}

var langfuseInputReaders = []string{"input.value", "span.input", "gen_ai.prompt", "gen_ai.input.messages", "gen_ai.tool.call.arguments"}

var langfuseOutputReaders = []string{"output.value", "span.output", "gen_ai.completion", "gen_ai.output.messages", "gen_ai.tool.call.result"}

// langfuseProviders mirrors MODEL_PROVIDER_MAP and guess_provider in
// futureagi/tracer/utils/adapters/base.py, in the same order.
var langfuseProviders = [][2]string{
	{"gpt-", "openai"}, {"o1", "openai"}, {"o3", "openai"}, {"o4", "openai"}, {"chatgpt-", "openai"},
	{"claude-", "anthropic"}, {"gemini-", "google"}, {"command-", "cohere"},
	{"mistral", "mistralai"}, {"mixtral", "mistralai"}, {"llama", "meta"}, {"deepseek", "deepseek"},
}

// isLangfuse mirrors the adapter's detect: any langfuse.* attribute.
func isLangfuse(attrs pcommon.Map) bool {
	found := false
	attrs.Range(func(key string, _ pcommon.Value) bool {
		found = strings.HasPrefix(key, "langfuse.")
		return !found
	})
	return found
}

// langfuseKind mirrors the adapter's _TYPE_MAP, and its LLM default for a
// span that names a model but no known type.
func langfuseKind(observationType, model string) string {
	switch observationType {
	case "generation":
		return "LLM"
	case "span":
		return "CHAIN"
	case "event":
		return "UNKNOWN"
	case "embedding", "agent", "tool", "chain", "retriever", "evaluator", "guardrail":
		return strings.ToUpper(observationType)
	}
	if model != "" {
		return "LLM"
	}
	return ""
}

func langfuseProvider(model string) string {
	model = strings.ToLower(model)
	for _, p := range langfuseProviders {
		if strings.HasPrefix(model, p[0]) {
			return p[1]
		}
	}
	return ""
}

// putLangfuseUsage maps usage_details, a JSON object of counts, onto the
// llm.token_count.* keys. Besides the adapter's input/output/total spellings
// it reads OpenAI's prompt_tokens / completion_tokens, which the SDK's OpenAI
// integration passes through unchanged. Without a total, input + output is
// the total, as in the adapter.
func putLangfuseUsage(attrs pcommon.Map, raw string) {
	var usage map[string]any
	if raw == "" || json.Unmarshal([]byte(raw), &usage) != nil {
		return
	}
	in, okIn := usageCount(usage, "input_tokens", "input", "prompt_tokens", "prompt")
	out, okOut := usageCount(usage, "output_tokens", "output", "completion_tokens", "completion")
	if okIn {
		attrs.PutInt("llm.token_count.prompt", in)
	}
	if okOut {
		attrs.PutInt("llm.token_count.completion", out)
	}
	if total, ok := usageCount(usage, "total_tokens", "total"); ok {
		attrs.PutInt("llm.token_count.total", total)
	} else if okIn && okOut {
		attrs.PutInt("llm.token_count.total", in+out)
	}
}

// usageCount returns the first key holding a number or an integer string.
func usageCount(usage map[string]any, keys ...string) (int64, bool) {
	for _, key := range keys {
		switch v := usage[key].(type) {
		case float64:
			return int64(v), true
		case string:
			if n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64); err == nil {
				return n, true
			}
		}
	}
	return 0, false
}

// putLangfuseContent writes input.value / output.value with a MIME type, as
// the adapter's set_io_value does: a JSON object or array stays JSON, a JSON
// string is unquoted, and anything else is text. The SDK sends a str input
// unencoded, so plain text is common. An explicit input.value, even empty,
// wins, as it does in the converter.
func putLangfuseContent(attrs pcommon.Map, from, to string, readers []string) {
	if _, explicit := attrs.Get(to + ".value"); explicit || has(attrs, readers...) {
		return
	}
	value, mime, ok := langfuseContent(String(attrs, from))
	if !ok {
		return
	}
	attrs.PutStr(to+".value", value)
	if !has(attrs, to+".mime_type") {
		attrs.PutStr(to+".mime_type", mime)
	}
}

func langfuseContent(raw string) (string, string, bool) {
	trimmed := strings.TrimSpace(raw)
	switch {
	case trimmed == "" || trimmed == "null":
		return "", "", false
	case !json.Valid([]byte(trimmed)):
		return raw, "text/plain", true
	case trimmed[0] == '{' || trimmed[0] == '[':
		return raw, "application/json", true
	case trimmed[0] == '"':
		var text string
		if json.Unmarshal([]byte(trimmed), &text) != nil || text == "" {
			return "", "", false
		}
		return text, "text/plain", true
	}
	return trimmed, "text/plain", true
}

// putLangfuseMetadata writes metadata from langfuse.observation.metadata, or
// else from its flattened langfuse.observation.metadata.<key> form as one
// JSON object. SDK 5.2.0 JSON-encodes each flattened value, strings included,
// so a value that is valid JSON is decoded first, as the adapter does.
func putLangfuseMetadata(attrs pcommon.Map) {
	const prefix = "langfuse.observation.metadata"
	if _, ok := attrs.Get(prefix); ok {
		copyAbsent(attrs, prefix, "metadata")
		return
	}
	flat := map[string]any{}
	attrs.Range(func(key string, v pcommon.Value) bool {
		if name, ok := strings.CutPrefix(key, prefix+"."); ok && name != "" {
			if v.Type() == pcommon.ValueTypeStr && json.Valid([]byte(v.Str())) {
				flat[name] = json.RawMessage(v.Str())
			} else {
				flat[name] = v.AsRaw()
			}
		}
		return true
	})
	if len(flat) == 0 {
		return
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if enc.Encode(flat) == nil {
		attrs.PutStr("metadata", strings.TrimSuffix(buf.String(), "\n"))
	}
}

// copyAbsent copies from into to when from holds a value and to does not.
// The value goes through a scratch copy: putting a key can move the map's
// backing array under a value read from it.
func copyAbsent(attrs pcommon.Map, from, to string) {
	v, ok := attrs.Get(from)
	if !ok || !usable(v) || has(attrs, to) {
		return
	}
	value := pcommon.NewValueEmpty()
	v.CopyTo(value)
	value.CopyTo(attrs.PutEmpty(to))
}

// has reports whether any key holds a usable value.
func has(attrs pcommon.Map, keys ...string) bool {
	for _, key := range keys {
		if v, ok := attrs.Get(key); ok && usable(v) {
			return true
		}
	}
	return false
}

// usable is false for an empty value or an empty string, which the
// converter's readers skip too.
func usable(v pcommon.Value) bool {
	switch v.Type() {
	case pcommon.ValueTypeEmpty:
		return false
	case pcommon.ValueTypeStr:
		return v.Str() != ""
	}
	return true
}
