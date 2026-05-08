package models

import (
	"encoding/json"
	"testing"
)

func TestCompletionRequest_PromptString(t *testing.T) {
	tests := []struct {
		name     string
		prompt   string // raw JSON
		expected string
	}{
		{
			name:     "string prompt",
			prompt:   `"Hello, world!"`,
			expected: "Hello, world!",
		},
		{
			name:     "string array prompt",
			prompt:   `["Hello", "World"]`,
			expected: "Hello\nWorld",
		},
		{
			name:     "single element array",
			prompt:   `["Just one"]`,
			expected: "Just one",
		},
		{
			name:     "empty string",
			prompt:   `""`,
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := &CompletionRequest{
				Prompt: json.RawMessage(tt.prompt),
			}
			got := req.PromptString()
			if got != tt.expected {
				t.Errorf("PromptString() = %q, want %q", got, tt.expected)
			}
		})
	}
}

func TestCompletionRequest_PromptString_Nil(t *testing.T) {
	req := &CompletionRequest{}
	if got := req.PromptString(); got != "" {
		t.Errorf("PromptString() with nil prompt = %q, want empty", got)
	}
}

func TestCompletionRequest_ToChatRequest(t *testing.T) {
	temp := 0.7
	maxTok := 100
	req := &CompletionRequest{
		Model:       "gpt-3.5-turbo-instruct",
		Prompt:      json.RawMessage(`"Say hello"`),
		Temperature: &temp,
		MaxTokens:   &maxTok,
		User:        "user-123",
	}

	chatReq := req.ToChatRequest()

	if chatReq.Model != "gpt-3.5-turbo-instruct" {
		t.Errorf("Model = %q, want %q", chatReq.Model, "gpt-3.5-turbo-instruct")
	}
	if len(chatReq.Messages) != 1 {
		t.Fatalf("Messages length = %d, want 1", len(chatReq.Messages))
	}
	if chatReq.Messages[0].Role != "user" {
		t.Errorf("Message role = %q, want %q", chatReq.Messages[0].Role, "user")
	}

	var content string
	if err := json.Unmarshal(chatReq.Messages[0].Content, &content); err != nil {
		t.Fatalf("failed to unmarshal message content: %v", err)
	}
	if content != "Say hello" {
		t.Errorf("Message content = %q, want %q", content, "Say hello")
	}

	if chatReq.Temperature == nil || *chatReq.Temperature != 0.7 {
		t.Error("Temperature not propagated correctly")
	}
	if chatReq.MaxTokens == nil || *chatReq.MaxTokens != 100 {
		t.Error("MaxTokens not propagated correctly")
	}
	if chatReq.User != "user-123" {
		t.Errorf("User = %q, want %q", chatReq.User, "user-123")
	}
}

func TestCompletionResponseFromChat(t *testing.T) {
	content, _ := json.Marshal("Hello there!")
	chatResp := &ChatCompletionResponse{
		ID:      "chatcmpl-123",
		Object:  "chat.completion",
		Created: 1700000000,
		Model:   "gpt-3.5-turbo",
		Choices: []Choice{
			{
				Index:        0,
				Message:      Message{Role: "assistant", Content: content},
				FinishReason: "stop",
			},
		},
		Usage: &Usage{
			PromptTokens:     5,
			CompletionTokens: 3,
			TotalTokens:      8,
		},
	}

	resp := CompletionResponseFromChat(chatResp)

	if resp.Object != "text_completion" {
		t.Errorf("Object = %q, want %q", resp.Object, "text_completion")
	}
	if resp.ID != "chatcmpl-123" {
		t.Errorf("ID = %q, want %q", resp.ID, "chatcmpl-123")
	}
	if len(resp.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(resp.Choices))
	}
	if resp.Choices[0].Text != "Hello there!" {
		t.Errorf("Text = %q, want %q", resp.Choices[0].Text, "Hello there!")
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want %q", resp.Choices[0].FinishReason, "stop")
	}
	if resp.Usage == nil || resp.Usage.TotalTokens != 8 {
		t.Error("Usage not propagated correctly")
	}
}

func TestCompletionStreamChunkFromChat(t *testing.T) {
	content := "Hi"
	finish := "stop"
	chunk := StreamChunk{
		ID:      "chatcmpl-456",
		Object:  "chat.completion.chunk",
		Created: 1700000001,
		Model:   "gpt-3.5-turbo",
		Choices: []StreamChoice{
			{
				Index: 0,
				Delta: Delta{Content: &content},
			},
			{
				Index:        1,
				Delta:        Delta{},
				FinishReason: &finish,
			},
		},
	}

	completionChunk := CompletionStreamChunkFromChat(chunk)

	if completionChunk.Object != "text_completion" {
		t.Errorf("Object = %q, want %q", completionChunk.Object, "text_completion")
	}
	if len(completionChunk.Choices) != 2 {
		t.Fatalf("Choices length = %d, want 2", len(completionChunk.Choices))
	}
	if completionChunk.Choices[0].Text != "Hi" {
		t.Errorf("Choices[0].Text = %q, want %q", completionChunk.Choices[0].Text, "Hi")
	}
	if completionChunk.Choices[1].Text != "" {
		t.Errorf("Choices[1].Text = %q, want empty", completionChunk.Choices[1].Text)
	}
	if completionChunk.Choices[1].FinishReason == nil || *completionChunk.Choices[1].FinishReason != "stop" {
		t.Error("FinishReason not propagated for choice 1")
	}
}

func TestCompletionRequest_JSON_RoundTrip(t *testing.T) {
	input := `{"model":"gpt-3.5-turbo-instruct","prompt":"Hello","max_tokens":50,"temperature":0.5,"stream":false}`

	var req CompletionRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal failed: %v", err)
	}

	if req.Model != "gpt-3.5-turbo-instruct" {
		t.Errorf("Model = %q", req.Model)
	}
	if req.PromptString() != "Hello" {
		t.Errorf("PromptString() = %q", req.PromptString())
	}
	if req.MaxTokens == nil || *req.MaxTokens != 50 {
		t.Error("MaxTokens not parsed")
	}
	if req.Stream {
		t.Error("Stream should be false")
	}
}
