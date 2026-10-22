package models

import (
	"encoding/json"
	"slices"
)

// ChatCompletionRequest represents an OpenAI-compatible chat completion request.
type ChatCompletionRequest struct {
	Model               string          `json:"model"`
	Messages            []Message       `json:"messages"`
	Temperature         *float64        `json:"temperature,omitempty"`
	TopP                *float64        `json:"top_p,omitempty"`
	N                   *int            `json:"n,omitempty"`
	Stream              bool            `json:"stream,omitempty"`
	StreamOptions       *StreamOptions  `json:"stream_options,omitempty"`
	Stop                json.RawMessage `json:"stop,omitempty"`
	MaxTokens           *int            `json:"max_tokens,omitempty"`
	MaxCompletionTokens *int            `json:"max_completion_tokens,omitempty"`
	PresencePenalty     *float64        `json:"presence_penalty,omitempty"`
	FrequencyPenalty    *float64        `json:"frequency_penalty,omitempty"`
	LogitBias           map[string]int  `json:"logit_bias,omitempty"`
	Logprobs            *bool           `json:"logprobs,omitempty"`
	TopLogprobs         *int            `json:"top_logprobs,omitempty"`
	User                string          `json:"user,omitempty"`
	Seed                *int            `json:"seed,omitempty"`
	Tools               []Tool          `json:"tools,omitempty"`
	ToolChoice          json.RawMessage `json:"tool_choice,omitempty"`
	ResponseFormat      *ResponseFormat `json:"response_format,omitempty"`
	ServiceTier         string          `json:"service_tier,omitempty"`
	Modalities          []string        `json:"modalities,omitempty"`
	Audio               *AudioConfig    `json:"audio,omitempty"`

	// Extra captures unknown fields for pass-through to providers.
	Extra map[string]json.RawMessage `json:"-"`
}

// AudioConfig represents OpenAI's audio configuration for chat completions
// with audio output modality.
type AudioConfig struct {
	Voice  string `json:"voice,omitempty"`
	Format string `json:"format,omitempty"` // "pcm16"|"mp3"|"opus"|"flac"|"wav"
}

// UnmarshalJSON implements custom unmarshaling to capture unknown fields.
func (r *ChatCompletionRequest) UnmarshalJSON(data []byte) error {
	// Unmarshal known fields using a type alias to avoid recursion.
	type Alias ChatCompletionRequest
	aux := &struct {
		*Alias
	}{
		Alias: (*Alias)(r),
	}
	if err := json.Unmarshal(data, aux); err != nil {
		return err
	}

	// Unmarshal all fields into a map to find extras.
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}

	known := map[string]bool{
		"model": true, "messages": true, "temperature": true, "top_p": true,
		"n": true, "stream": true, "stream_options": true, "stop": true,
		"max_tokens": true, "max_completion_tokens": true,
		"presence_penalty": true, "frequency_penalty": true,
		"logit_bias": true, "logprobs": true, "top_logprobs": true,
		"user": true, "seed": true, "tools": true, "tool_choice": true,
		"response_format": true, "service_tier": true,
		"modalities": true, "audio": true,
	}

	for k, v := range raw {
		if !known[k] {
			if r.Extra == nil {
				r.Extra = make(map[string]json.RawMessage)
			}
			r.Extra[k] = v
		}
	}

	return nil
}

// MarshalJSON implements custom marshaling to include extra fields.
func (r ChatCompletionRequest) MarshalJSON() ([]byte, error) {
	type Alias ChatCompletionRequest
	data, err := json.Marshal((*Alias)(&r))
	if err != nil {
		return nil, err
	}
	if len(r.Extra) == 0 {
		return data, nil
	}
	// Merge extra fields into the JSON object.
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil, err
	}
	for k, v := range r.Extra {
		obj[k] = v
	}
	return json.Marshal(obj)
}

type Message struct {
	Role       string          `json:"role"`
	Content    json.RawMessage `json:"content,omitempty"`
	Name       string          `json:"name,omitempty"`
	ToolCalls  []ToolCall      `json:"tool_calls,omitempty"`
	ToolCallID string          `json:"tool_call_id,omitempty"`
	// ThinkingBlocks carries Anthropic extended-thinking content blocks on the
	// canonical OpenAI Message.  Non-standard field (LiteLLM convention); present
	// only when the upstream provider returned thinking blocks.  JSON key is
	// "thinking_blocks" (omitempty) so round-trips through providers that do not
	// understand it are silent.
	ThinkingBlocks json.RawMessage `json:"thinking_blocks,omitempty"`
	// ReasoningContent carries the model's surfaced thinking summary (Gemini
	// thought summaries, DeepSeek/LiteLLM convention). Non-standard OpenAI
	// field, omitempty so it's silent for providers that don't emit it.
	ReasoningContent string `json:"reasoning_content,omitempty"`
	// Annotations carries source citations in OpenAI's own url_citation shape,
	// which is what its web-search responses use.  Populated from Anthropic's
	// web_search_result_location citations; omitempty so it is silent for
	// providers and requests that produce none.
	Annotations json.RawMessage `json:"annotations,omitempty"`
}

type ToolCall struct {
	ID       string       `json:"id"`
	Type     string       `json:"type"`
	Function FunctionCall `json:"function"`
}

type FunctionCall struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type Tool struct {
	Type     string       `json:"type"`
	Function ToolFunction `json:"function"`

	// Raw holds the caller's original bytes for a tool that is not a plain
	// "function" — Anthropic's server tools (web_search_20250305 and the
	// versions after it, code_execution, ...) are executed by the provider and
	// carry their own fields: max_uses, allowed_domains, user_location.  None
	// of those fit ToolFunction, so re-marshalling this struct would silently
	// strip them.  Keeping the bytes lets the entry reach the provider exactly
	// as it was written, and lets a provider that does not support it answer
	// with its own error instead of the gateway inventing one.
	Raw json.RawMessage `json:"-"`
}

// UnmarshalJSON keeps the original bytes of a non-function tool.  New server
// tool types ship regularly, so the test is "not a function" rather than a list
// of known type strings that would need editing for each one.
func (t *Tool) UnmarshalJSON(data []byte) error {
	type toolAlias Tool
	var a toolAlias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	*t = Tool(a)
	// Keep the caller's bytes for anything that is not a plain function tool.
	// A declared non-function type is one such entry. So is an entry that
	// names no function at all: Gemini's provider-executed tools are written
	// as a bare {"google_search": {}}, with no type to declare, and without
	// its own bytes an entry like that reaches the provider as an empty
	// function and is dropped.
	if (t.Type != "" && t.Type != "function") || t.Function.Name == "" {
		t.Raw = append(json.RawMessage(nil), data...)
	}
	return nil
}

// builtinCapabilityAliases maps every spelling of a provider-executed tool to
// the one name it is known by.
//
// The Google SDKs write these in camelCase and the OpenAI-compatible clients
// in snake_case, and a provider treats both as the same capability. So a
// policy must too: with only the supplied spelling to go on, deny:
// [code_execution] let {"codeExecution":{}} straight through to the upstream.
var builtinCapabilityAliases = map[string]string{
	"google_search":           "google_search",
	"googleSearch":            "google_search",
	"google_search_retrieval": "google_search_retrieval",
	"googleSearchRetrieval":   "google_search_retrieval",
	"url_context":             "url_context",
	"urlContext":              "url_context",
	"code_execution":          "code_execution",
	"codeExecution":           "code_execution",
}

// CanonicalCapability returns the one name a built-in capability is known by.
//
// Every side of an authorization decision must call this: the identity read
// off a request, and each entry of an allow or deny list an operator wrote.
// Normalizing one side alone leaves the other spelling unmatched, which is a
// bypass rather than a cosmetic inconsistency. A name it does not recognize —
// an ordinary function, a server tool belonging to another provider — is
// returned unchanged, so function-name semantics are untouched.
func CanonicalCapability(name string) string {
	if canonical, ok := builtinCapabilityAliases[name]; ok {
		return canonical
	}
	return name
}

// CapabilitySpellings returns every name a capability is written as, its
// canonical one first.
//
// For a matcher that takes glob patterns rather than exact names, canonicalizing
// alone is not enough: an operator who denied "*Execution" would stop matching
// once the name became code_execution. Testing each spelling keeps a pattern
// written either way working, and a denylist fails closed on any of them.
func CapabilitySpellings(name string) []string {
	canonical := CanonicalCapability(name)
	spellings := []string{canonical}
	for spelling, c := range builtinCapabilityAliases {
		if c == canonical && spelling != canonical {
			spellings = append(spellings, spelling)
		}
	}
	if name != canonical {
		// The name as supplied, when it is not one of the known aliases.
		if !slices.Contains(spellings, name) {
			spellings = append(spellings, name)
		}
	}
	return spellings
}

// CanonicalCapabilities normalizes a policy list in place of the caller
// writing the loop.
func CanonicalCapabilities(names []string) []string {
	out := make([]string, 0, len(names))
	for _, name := range names {
		out = append(out, CanonicalCapability(name))
	}
	return out
}

// Capability reports the one thing a tools entry asks the provider to do: the
// function it declares, or the provider-executed tool it names.
//
// Policy and provider must read a tool's identity from here, and only from
// here. They used to disagree: the tool-policy plugin read Function.Name while
// a provider chose what to forward from Type, so an entry declaring
// {"type": "code_execution", "function": {"name": "get_weather"}} passed a
// policy that allowed get_weather and then forwarded code_execution. An entry
// written as a bare {"google_search": {}} names no function at all, so a
// policy denying google_search never matched it.
//
// A declared type that is not "function" wins, because that is what a provider
// forwards. ContradictsItsType reports the entry that declares both.
func (t Tool) Capability() string {
	if t.Type != "" && t.Type != "function" {
		return CanonicalCapability(t.Type)
	}
	if t.Function.Name != "" {
		// An application's own function name, exactly as written. Resolving it
		// through the alias table would make a custom function called
		// "codeExecution" the same identity as the built-in code_execution,
		// and a policy naming either would then cover both.
		return t.Function.Name
	}
	return CanonicalCapability(t.rawKey())
}

// NamesBuiltinCapability reports whether this entry asks for a tool the
// provider runs itself, rather than an application function.
//
// Alias equivalence applies to those and only to those: two spellings of
// google_search are one capability, while two spellings of a function name are
// two different functions.
func (t Tool) NamesBuiltinCapability() bool {
	if t.IsFunction() {
		return false
	}
	name := t.Type
	if name == "" || name == "function" {
		name = t.rawKey()
	}
	_, known := builtinCapabilityAliases[name]
	return known
}

// IsBuiltinCapability reports whether a name is a recognized spelling of a
// tool the provider runs itself.
func IsBuiltinCapability(name string) bool {
	_, known := builtinCapabilityAliases[name]
	return known
}

// IsFunction reports whether the entry declares a plain function, which is
// the one shape the canonical struct describes in full.
func (t Tool) IsFunction() bool {
	return (t.Type == "function" || t.Type == "") && t.Function.Name != ""
}

// rawKey reads the capability out of an entry that names it as its only key,
// which is how Gemini's own tools are written.
func (t Tool) rawKey() string {
	if len(t.Raw) == 0 {
		return ""
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(t.Raw, &fields) != nil {
		return ""
	}
	// A single-key object names its capability. More than one key, and there
	// is nothing to pick, so the entry has no identity to enforce against.
	if len(fields) != 1 {
		return ""
	}
	for name := range fields {
		return name
	}
	return ""
}

// ContradictsItsType reports an entry that declares a non-function capability
// and a function name at the same time. Such an entry asks for two different
// things, and whichever one a reader picks, the other is silently ignored.
func (t Tool) ContradictsItsType() bool {
	return t.Type != "" && t.Type != "function" && t.Function.Name != ""
}

// MarshalJSON replays those bytes, so a server tool survives the round trip
// through the canonical struct on every provider path.
func (t Tool) MarshalJSON() ([]byte, error) {
	if len(t.Raw) > 0 {
		return t.Raw, nil
	}
	type toolAlias Tool
	return json.Marshal(toolAlias(t))
}

type ToolFunction struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
	Strict      *bool           `json:"strict,omitempty"`
}

type ResponseFormat struct {
	Type       string          `json:"type"`
	JSONSchema json.RawMessage `json:"json_schema,omitempty"`
}

type StreamOptions struct {
	IncludeUsage bool `json:"include_usage,omitempty"`
}
