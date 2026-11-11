package models

import (
	"encoding/json"
	"testing"
)

// decodeMessage marshals a Choice and returns the raw "message" object, so that
// the ABSENCE of a key is observable — a typed struct would silently fill it in.
func decodeMessage(t *testing.T, c Choice) map[string]json.RawMessage {
	t.Helper()
	data, err := json.Marshal(c)
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}
	var choice struct {
		Message map[string]json.RawMessage `json:"message"`
	}
	if err := json.Unmarshal(data, &choice); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}
	return choice.Message
}

func TestChoiceMarshalAlwaysEmitsContent(t *testing.T) {
	toolCall := ToolCall{
		ID:       "call_0",
		Type:     "function",
		Function: FunctionCall{Name: "get_weather", Arguments: `{"city":"SF"}`},
	}

	tests := []struct {
		name        string
		message     Message
		wantContent string
	}{
		{
			name:        "tool calls only: null, as OpenAI sends",
			message:     Message{Role: "assistant", ToolCalls: []ToolCall{toolCall}},
			wantContent: `null`,
		},
		{
			name:        "no text and no tool calls: empty string, as OpenAI sends when reasoning consumed the budget",
			message:     Message{Role: "assistant", ReasoningContent: "thinking..."},
			wantContent: `""`,
		},
		{
			name:        "text is passed through untouched",
			message:     Message{Role: "assistant", Content: json.RawMessage(`"hello"`)},
			wantContent: `"hello"`,
		},
		{
			name:        "text alongside tool calls is passed through untouched",
			message:     Message{Role: "assistant", Content: json.RawMessage(`"on it"`), ToolCalls: []ToolCall{toolCall}},
			wantContent: `"on it"`,
		},
		{
			name:        "explicit null from an upstream that already sent one is preserved",
			message:     Message{Role: "assistant", Content: json.RawMessage(`null`), ToolCalls: []ToolCall{toolCall}},
			wantContent: `null`,
		},
		{
			name:        "multipart content is passed through untouched",
			message:     Message{Role: "assistant", Content: json.RawMessage(`[{"type":"text","text":"hi"}]`)},
			wantContent: `[{"type":"text","text":"hi"}]`,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			msg := decodeMessage(t, Choice{Index: 0, Message: tt.message, FinishReason: "stop"})

			got, ok := msg["content"]
			if !ok {
				t.Fatalf("content key absent; OpenAI always emits one")
			}
			if string(got) != tt.wantContent {
				t.Errorf("content = %s, want %s", got, tt.wantContent)
			}
		})
	}
}

// The caller's Choice must not be mutated: providers hand the same response to
// the plugin chain (logging, cache) after it is written.
func TestChoiceMarshalDoesNotMutateCaller(t *testing.T) {
	c := Choice{
		Index:        0,
		Message:      Message{Role: "assistant", ToolCalls: []ToolCall{{ID: "call_0", Type: "function"}}},
		FinishReason: "tool_calls",
	}

	if _, err := json.Marshal(c); err != nil {
		t.Fatalf("Marshal error: %v", err)
	}
	if c.Message.Content != nil {
		t.Errorf("Content = %s, want nil: marshaling must not mutate the caller's Choice", c.Message.Content)
	}
}

// The request path must keep omitting content — a null content on an outbound
// user message is rejected upstream. This is why the fix lives on Choice and not
// on Message.
func TestRequestMessageStillOmitsEmptyContent(t *testing.T) {
	data, err := json.Marshal(ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []Message{{Role: "assistant", ToolCalls: []ToolCall{{ID: "call_0", Type: "function"}}}},
	})
	if err != nil {
		t.Fatalf("Marshal error: %v", err)
	}

	var req struct {
		Messages []map[string]json.RawMessage `json:"messages"`
	}
	if err := json.Unmarshal(data, &req); err != nil {
		t.Fatalf("Unmarshal error: %v", err)
	}
	if _, ok := req.Messages[0]["content"]; ok {
		t.Errorf("request message gained a content key: %s", data)
	}
}
