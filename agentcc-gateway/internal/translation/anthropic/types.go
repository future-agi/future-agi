// Package anthropic — local Anthropic wire-format types.
//
// These cover only what we need for inbound translation. The full Anthropic
// Messages API spec is at https://platform.claude.com/docs/en/api/messages.
package anthropic

import "encoding/json"

// ─── Request types ────────────────────────────────────────────────────────────

// MessagesRequest is the top-level body for POST /v1/messages.
type MessagesRequest struct {
	Model     string           `json:"model"`
	Messages  []AnthropicMsg   `json:"messages"`
	MaxTokens int              `json:"max_tokens"`
	System    json.RawMessage  `json:"system,omitempty"` // string or []TextBlockParam
	Tools     []AnthropicTool  `json:"tools,omitempty"`
	ToolChoice json.RawMessage `json:"tool_choice,omitempty"`
	Stream    bool             `json:"stream,omitempty"`

	// Sampling params.
	Temperature *float64 `json:"temperature,omitempty"`
	TopP        *float64 `json:"top_p,omitempty"`
	TopK        *int     `json:"top_k,omitempty"` // Anthropic-only, no OpenAI equiv

	// Stop sequences ([]string in Anthropic, vs string|[]string in OpenAI).
	StopSequences []string `json:"stop_sequences,omitempty"`

	// Extended thinking config.
	Thinking *ThinkingConfig `json:"thinking,omitempty"`

	// Metadata.
	Metadata *RequestMetadata `json:"metadata,omitempty"`
}

// ThinkingConfig controls extended thinking.
type ThinkingConfig struct {
	Type         string `json:"type"`          // "enabled" or "disabled"
	BudgetTokens int    `json:"budget_tokens"` // ignored when type == "disabled"
}

// RequestMetadata holds optional user-id / request metadata.
type RequestMetadata struct {
	UserID string `json:"user_id,omitempty"`
}

// AnthropicMsg is a single turn in a Messages conversation.
type AnthropicMsg struct {
	Role    string          `json:"role"`    // "user" or "assistant"
	Content json.RawMessage `json:"content"` // string or []ContentBlock
}

// ContentBlock is a polymorphic content block inside a message.
// The "type" discriminator selects among text/image/tool_use/tool_result/thinking.
type ContentBlock struct {
	Type string `json:"type"`

	// text
	Text string `json:"text,omitempty"`

	// image
	Source *ImageSource `json:"source,omitempty"`

	// tool_use (assistant side)
	ID    string          `json:"id,omitempty"`
	Name  string          `json:"name,omitempty"`
	Input json.RawMessage `json:"input,omitempty"`

	// tool_result (user side)
	ToolUseID string          `json:"tool_use_id,omitempty"`
	Content   json.RawMessage `json:"content,omitempty"` // string or []ContentBlock

	// thinking
	Thinking  string `json:"thinking,omitempty"`
	Signature string `json:"signature,omitempty"`

	// cache_control (Anthropic prompt-caching)
	CacheControl *CacheControl `json:"cache_control,omitempty"`
}

// TextBlockParam is the typed form used for the system parameter when it is
// an array (as opposed to a bare string).
type TextBlockParam struct {
	Type         string        `json:"type"` // "text"
	Text         string        `json:"text"`
	CacheControl *CacheControl `json:"cache_control,omitempty"`
}

// ImageSource holds the image data for a vision content block.
type ImageSource struct {
	Type      string `json:"type"`       // "base64" or "url"
	MediaType string `json:"media_type"` // e.g. "image/jpeg"
	Data      string `json:"data,omitempty"`
	URL       string `json:"url,omitempty"`
}

// CacheControl marks a content block for prompt caching.
type CacheControl struct {
	Type string `json:"type"` // "ephemeral"
}

// AnthropicTool is a function tool definition.
type AnthropicTool struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	InputSchema json.RawMessage `json:"input_schema"`

	// cache_control is supported at the tool level.
	CacheControl *CacheControl `json:"cache_control,omitempty"`
}

// ─── Response types ───────────────────────────────────────────────────────────

// MessagesResponse is the top-level body for a non-streaming response.
type MessagesResponse struct {
	ID           string         `json:"id"`
	Type         string         `json:"type"` // "message"
	Role         string         `json:"role"` // "assistant"
	Content      []ContentBlock `json:"content"`
	Model        string         `json:"model"`
	StopReason   string         `json:"stop_reason"`
	StopSequence *string        `json:"stop_sequence,omitempty"`
	Usage        ResponseUsage  `json:"usage"`
}

// ResponseUsage matches Anthropic's usage object.
type ResponseUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// ─── Stream event types ───────────────────────────────────────────────────────

// SSEEvent is an Anthropic SSE envelope.
type SSEEvent struct {
	Type string      `json:"type"`
	Data interface{} `json:"-"` // marshaled separately
}

// MessageStartEvent opens the stream.
type MessageStartEvent struct {
	Type    string          `json:"type"` // "message_start"
	Message MessageStartMsg `json:"message"`
}

// MessageStartMsg is the partial message in message_start.
type MessageStartMsg struct {
	ID    string        `json:"id"`
	Type  string        `json:"type"` // "message"
	Role  string        `json:"role"` // "assistant"
	Model string        `json:"model,omitempty"`
	Usage ResponseUsage `json:"usage"`
}

// ContentBlockStartEvent announces a new content block.
type ContentBlockStartEvent struct {
	Type         string       `json:"type"`  // "content_block_start"
	Index        int          `json:"index"`
	ContentBlock ContentBlock `json:"content_block"`
}

// ContentBlockDeltaEvent carries an incremental delta.
type ContentBlockDeltaEvent struct {
	Type  string        `json:"type"`  // "content_block_delta"
	Index int           `json:"index"`
	Delta ContentDelta  `json:"delta"`
}

// ContentBlockStopEvent closes a content block.
type ContentBlockStopEvent struct {
	Type  string `json:"type"`  // "content_block_stop"
	Index int    `json:"index"`
}

// ContentDelta holds the actual increment; type discriminates.
type ContentDelta struct {
	Type        string `json:"type"`                  // "text_delta" | "input_json_delta" | "thinking_delta"
	Text        string `json:"text,omitempty"`        // text_delta
	PartialJSON string `json:"partial_json,omitempty"` // input_json_delta
	Thinking    string `json:"thinking,omitempty"`    // thinking_delta
}

// MessageDeltaEvent carries final stop-reason and output usage.
type MessageDeltaEvent struct {
	Type  string       `json:"type"`  // "message_delta"
	Delta MessageDelta `json:"delta"`
	Usage ResponseUsage `json:"usage"`
}

// MessageDelta is the payload inside MessageDeltaEvent.
type MessageDelta struct {
	StopReason   string  `json:"stop_reason"`
	StopSequence *string `json:"stop_sequence,omitempty"`
}

// MessageStopEvent terminates the stream.
type MessageStopEvent struct {
	Type string `json:"type"` // "message_stop"
}

// StreamErrorEvent carries a mid-stream error.
type StreamErrorEvent struct {
	Type  string      `json:"type"` // "error"
	Error StreamError `json:"error"`
}

// StreamError is the nested error body.
type StreamError struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// ─── Error response types ─────────────────────────────────────────────────────

// ErrorResponse is the Anthropic-format error body.
type ErrorResponse struct {
	Type  string      `json:"type"` // "error"
	Error ErrorDetail `json:"error"`
}

// ErrorDetail is the nested error detail.
type ErrorDetail struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}
