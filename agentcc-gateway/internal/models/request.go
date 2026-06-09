package models

import "encoding/json"

// ChatCompletionRequest represents an OpenAI-compatible chat completion request.
type ChatCompletionRequest struct {
	Model            string          `json:"model"`
	Messages         []Message       `json:"messages"`
	Temperature      *float64        `json:"temperature,omitempty"`
	TopP             *float64        `json:"top_p,omitempty"`
	N                *int            `json:"n,omitempty"`
	Stream           bool            `json:"stream,omitempty"`
	StreamOptions    *StreamOptions  `json:"stream_options,omitempty"`
	Stop             json.RawMessage `json:"stop,omitempty"`
	MaxTokens        *int            `json:"max_tokens,omitempty"`
	MaxCompletionTokens *int         `json:"max_completion_tokens,omitempty"`
	PresencePenalty  *float64        `json:"presence_penalty,omitempty"`
	FrequencyPenalty *float64        `json:"frequency_penalty,omitempty"`
	LogitBias        map[string]int  `json:"logit_bias,omitempty"`
	Logprobs         *bool           `json:"logprobs,omitempty"`
	TopLogprobs      *int            `json:"top_logprobs,omitempty"`
	User             string          `json:"user,omitempty"`
	Seed             *int            `json:"seed,omitempty"`
	Tools            []Tool          `json:"tools,omitempty"`
	ToolChoice       json.RawMessage `json:"tool_choice,omitempty"`
	ResponseFormat   *ResponseFormat `json:"response_format,omitempty"`
	ServiceTier      string          `json:"service_tier,omitempty"`
	Modalities       []string        `json:"modalities,omitempty"`
	Audio            *AudioConfig    `json:"audio,omitempty"`

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
	Role           string          `json:"role"`
	Content        json.RawMessage `json:"content,omitempty"`
	Name           string          `json:"name,omitempty"`
	ToolCalls      []ToolCall      `json:"tool_calls,omitempty"`
	ToolCallID     string          `json:"tool_call_id,omitempty"`
	// ThinkingBlocks carries Anthropic extended-thinking content blocks on the
	// canonical OpenAI Message.  Non-standard field (LiteLLM convention); present
	// only when the upstream provider returned thinking blocks.  JSON key is
	// "thinking_blocks" (omitempty) so round-trips through providers that do not
	// understand it are silent.
	ThinkingBlocks json.RawMessage `json:"thinking_blocks,omitempty"`
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
