package models

import "encoding/json"

// CompletionRequest represents an OpenAI-compatible legacy text completion request.
// This is the /v1/completions format (prompt-based, not message-based).
type CompletionRequest struct {
	Model            string          `json:"model"`
	Prompt           json.RawMessage `json:"prompt"`
	MaxTokens        *int            `json:"max_tokens,omitempty"`
	Temperature      *float64        `json:"temperature,omitempty"`
	TopP             *float64        `json:"top_p,omitempty"`
	N                *int            `json:"n,omitempty"`
	Stream           bool            `json:"stream,omitempty"`
	StreamOptions    *StreamOptions  `json:"stream_options,omitempty"`
	Stop             json.RawMessage `json:"stop,omitempty"`
	PresencePenalty  *float64        `json:"presence_penalty,omitempty"`
	FrequencyPenalty *float64        `json:"frequency_penalty,omitempty"`
	LogitBias        map[string]int  `json:"logit_bias,omitempty"`
	Logprobs         *int            `json:"logprobs,omitempty"`
	Echo             bool            `json:"echo,omitempty"`
	Suffix           string          `json:"suffix,omitempty"`
	BestOf           *int            `json:"best_of,omitempty"`
	User             string          `json:"user,omitempty"`
	Seed             *int            `json:"seed,omitempty"`
}

// PromptString extracts the prompt as a single string.
// Handles both string and string array formats.
func (r *CompletionRequest) PromptString() string {
	if r.Prompt == nil {
		return ""
	}

	// Try string first.
	var s string
	if err := json.Unmarshal(r.Prompt, &s); err == nil {
		return s
	}

	// Try string array — join with newlines.
	var arr []string
	if err := json.Unmarshal(r.Prompt, &arr); err == nil {
		result := ""
		for i, p := range arr {
			if i > 0 {
				result += "\n"
			}
			result += p
		}
		return result
	}

	return string(r.Prompt)
}

// ToChatRequest converts a legacy completion request to a chat completion request.
// The prompt is wrapped into a single user message.
func (r *CompletionRequest) ToChatRequest() *ChatCompletionRequest {
	promptStr := r.PromptString()
	content, _ := json.Marshal(promptStr)

	chatReq := &ChatCompletionRequest{
		Model: r.Model,
		Messages: []Message{
			{
				Role:    "user",
				Content: content,
			},
		},
		Temperature:      r.Temperature,
		TopP:             r.TopP,
		N:                r.N,
		Stream:           r.Stream,
		StreamOptions:    r.StreamOptions,
		Stop:             r.Stop,
		MaxTokens:        r.MaxTokens,
		PresencePenalty:  r.PresencePenalty,
		FrequencyPenalty: r.FrequencyPenalty,
		LogitBias:        r.LogitBias,
		User:             r.User,
		Seed:             r.Seed,
	}

	return chatReq
}

// CompletionResponse represents an OpenAI-compatible legacy text completion response.
type CompletionResponse struct {
	ID                string             `json:"id"`
	Object            string             `json:"object"`
	Created           int64              `json:"created"`
	Model             string             `json:"model"`
	Choices           []CompletionChoice `json:"choices"`
	Usage             *Usage             `json:"usage,omitempty"`
	SystemFingerprint string             `json:"system_fingerprint,omitempty"`
}

// CompletionChoice represents a single completion choice.
type CompletionChoice struct {
	Index        int              `json:"index"`
	Text         string           `json:"text"`
	FinishReason string           `json:"finish_reason"`
	Logprobs     *json.RawMessage `json:"logprobs"`
}

// CompletionResponseFromChat converts a chat completion response to the legacy format.
func CompletionResponseFromChat(chatResp *ChatCompletionResponse) *CompletionResponse {
	choices := make([]CompletionChoice, len(chatResp.Choices))
	for i, c := range chatResp.Choices {
		// Extract text content from the message.
		text := ""
		if c.Message.Content != nil {
			var s string
			if err := json.Unmarshal(c.Message.Content, &s); err == nil {
				text = s
			} else {
				text = string(c.Message.Content)
			}
		}

		choices[i] = CompletionChoice{
			Index:        c.Index,
			Text:         text,
			FinishReason: c.FinishReason,
			Logprobs:     c.Logprobs,
		}
	}

	return &CompletionResponse{
		ID:                chatResp.ID,
		Object:            "text_completion",
		Created:           chatResp.Created,
		Model:             chatResp.Model,
		Choices:           choices,
		Usage:             chatResp.Usage,
		SystemFingerprint: chatResp.SystemFingerprint,
	}
}

// CompletionStreamChunk represents a streaming chunk for the legacy completions format.
type CompletionStreamChunk struct {
	ID                string                   `json:"id"`
	Object            string                   `json:"object"`
	Created           int64                    `json:"created"`
	Model             string                   `json:"model"`
	Choices           []CompletionStreamChoice `json:"choices"`
	Usage             *Usage                   `json:"usage,omitempty"`
	SystemFingerprint string                   `json:"system_fingerprint,omitempty"`
}

// CompletionStreamChoice represents a streaming choice in the legacy format.
type CompletionStreamChoice struct {
	Index        int     `json:"index"`
	Text         string  `json:"text"`
	FinishReason *string `json:"finish_reason"`
}

// CompletionStreamChunkFromChat converts a chat stream chunk to the legacy format.
func CompletionStreamChunkFromChat(chunk StreamChunk) CompletionStreamChunk {
	choices := make([]CompletionStreamChoice, len(chunk.Choices))
	for i, c := range chunk.Choices {
		text := ""
		if c.Delta.Content != nil {
			text = *c.Delta.Content
		}
		choices[i] = CompletionStreamChoice{
			Index:        c.Index,
			Text:         text,
			FinishReason: c.FinishReason,
		}
	}

	return CompletionStreamChunk{
		ID:                chunk.ID,
		Object:            "text_completion",
		Created:           chunk.Created,
		Model:             chunk.Model,
		Choices:           choices,
		Usage:             chunk.Usage,
		SystemFingerprint: chunk.SystemFingerprint,
	}
}
