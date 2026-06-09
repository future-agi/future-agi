package models

import (
	"encoding/json"
	"testing"
)

func TestChatCompletionRequestMarshalRoundTrip(t *testing.T) {
	input := `{
		"model": "gpt-4o",
		"messages": [
			{"role": "system", "content": "You are a helpful assistant."},
			{"role": "user", "content": "Hello!"}
		],
		"temperature": 0.7,
		"max_tokens": 100,
		"stream": false,
		"unknown_field": "should be preserved"
	}`

	var req ChatCompletionRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "gpt-4o" {
		t.Errorf("Model = %q, want %q", req.Model, "gpt-4o")
	}
	if len(req.Messages) != 2 {
		t.Errorf("Messages length = %d, want 2", len(req.Messages))
	}
	if req.Temperature == nil || *req.Temperature != 0.7 {
		t.Errorf("Temperature = %v, want 0.7", req.Temperature)
	}
	if req.MaxTokens == nil || *req.MaxTokens != 100 {
		t.Errorf("MaxTokens = %v, want 100", req.MaxTokens)
	}
	if req.Stream {
		t.Error("Stream should be false")
	}

	// Unknown fields preserved.
	if req.Extra == nil {
		t.Fatal("Extra should not be nil")
	}
	if _, ok := req.Extra["unknown_field"]; !ok {
		t.Error("unknown_field should be in Extra")
	}

	// Re-marshal and verify unknown_field is included.
	data, err := json.Marshal(&req)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var raw map[string]json.RawMessage
	json.Unmarshal(data, &raw)

	if _, ok := raw["unknown_field"]; !ok {
		t.Error("unknown_field should be in marshaled output")
	}
	if _, ok := raw["model"]; !ok {
		t.Error("model should be in marshaled output")
	}
}

func TestChatCompletionRequestMinimal(t *testing.T) {
	input := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`

	var req ChatCompletionRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if req.Model != "gpt-4o" {
		t.Errorf("Model = %q, want %q", req.Model, "gpt-4o")
	}
	if len(req.Messages) != 1 {
		t.Errorf("Messages length = %d, want 1", len(req.Messages))
	}
	if req.Extra != nil && len(req.Extra) > 0 {
		t.Errorf("Extra should be empty, got %v", req.Extra)
	}
}

func TestChatCompletionRequestWithTools(t *testing.T) {
	input := `{
		"model": "gpt-4o",
		"messages": [{"role": "user", "content": "What's the weather?"}],
		"tools": [
			{
				"type": "function",
				"function": {
					"name": "get_weather",
					"description": "Get weather info",
					"parameters": {"type": "object", "properties": {"city": {"type": "string"}}}
				}
			}
		],
		"tool_choice": "auto"
	}`

	var req ChatCompletionRequest
	if err := json.Unmarshal([]byte(input), &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}

	if len(req.Tools) != 1 {
		t.Fatalf("Tools length = %d, want 1", len(req.Tools))
	}
	if req.Tools[0].Function.Name != "get_weather" {
		t.Errorf("Tool name = %q, want %q", req.Tools[0].Function.Name, "get_weather")
	}
}
