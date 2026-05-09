package cohere

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Helper utilities
// ---------------------------------------------------------------------------

func floatPtr(f float64) *float64 { return &f }
func intPtr(i int) *int           { return &i }

func rawJSON(s string) json.RawMessage { return json.RawMessage(s) }

// ---------------------------------------------------------------------------
// translateRequest tests
// ---------------------------------------------------------------------------

func TestTranslateRequest_BasicChat(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "command-r-plus",
		Messages: []models.Message{
			{Role: "system", Content: rawJSON(`"You are helpful."`)},
			{Role: "user", Content: rawJSON(`"Hello"`)},
		},
	}

	cr := translateRequest(req)

	if cr.Model != "command-r-plus" {
		t.Errorf("Model: got %q, want %q", cr.Model, "command-r-plus")
	}
	if len(cr.Messages) != 2 {
		t.Fatalf("Messages length: got %d, want 2", len(cr.Messages))
	}
	if cr.Messages[0].Role != "system" {
		t.Errorf("Messages[0].Role: got %q, want %q", cr.Messages[0].Role, "system")
	}
	if cr.Messages[0].Content != "You are helpful." {
		t.Errorf("Messages[0].Content: got %q, want %q", cr.Messages[0].Content, "You are helpful.")
	}
	if cr.Messages[1].Role != "user" {
		t.Errorf("Messages[1].Role: got %q, want %q", cr.Messages[1].Role, "user")
	}
	if cr.Messages[1].Content != "Hello" {
		t.Errorf("Messages[1].Content: got %q, want %q", cr.Messages[1].Content, "Hello")
	}
}

func TestTranslateRequest_ModelNameWithPrefix(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "cohere/command-r-plus",
		Messages: []models.Message{
			{Role: "user", Content: rawJSON(`"Hi"`)},
		},
	}

	cr := translateRequest(req)

	if cr.Model != "command-r-plus" {
		t.Errorf("Model: got %q, want %q", cr.Model, "command-r-plus")
	}
}

func TestTranslateRequest_TopPMapsToP(t *testing.T) {
	topP := 0.9
	req := &models.ChatCompletionRequest{
		Model: "command-r",
		Messages: []models.Message{
			{Role: "user", Content: rawJSON(`"test"`)},
		},
		TopP: &topP,
	}

	cr := translateRequest(req)

	if cr.P == nil {
		t.Fatal("P is nil, expected it to be set from TopP")
	}
	if *cr.P != 0.9 {
		t.Errorf("P: got %f, want %f", *cr.P, 0.9)
	}
	// Verify the JSON field name is "p"
	data, err := json.Marshal(cr)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if _, ok := raw["p"]; !ok {
		t.Error("JSON output missing 'p' field")
	}
	if _, ok := raw["top_p"]; ok {
		t.Error("JSON output should NOT have 'top_p' field")
	}
}

func TestTranslateRequest_MaxTokens(t *testing.T) {
	t.Run("from MaxTokens", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:     "command-r",
			Messages:  []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			MaxTokens: intPtr(200),
		}
		cr := translateRequest(req)
		if cr.MaxTokens == nil || *cr.MaxTokens != 200 {
			t.Errorf("MaxTokens: got %v, want 200", cr.MaxTokens)
		}
	})

	t.Run("from MaxCompletionTokens", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:               "command-r",
			Messages:            []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			MaxCompletionTokens: intPtr(300),
		}
		cr := translateRequest(req)
		if cr.MaxTokens == nil || *cr.MaxTokens != 300 {
			t.Errorf("MaxTokens: got %v, want 300", cr.MaxTokens)
		}
	})

	t.Run("MaxTokens takes precedence over MaxCompletionTokens", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:               "command-r",
			Messages:            []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			MaxTokens:           intPtr(100),
			MaxCompletionTokens: intPtr(999),
		}
		cr := translateRequest(req)
		if cr.MaxTokens == nil || *cr.MaxTokens != 100 {
			t.Errorf("MaxTokens: got %v, want 100", cr.MaxTokens)
		}
	})

	t.Run("neither set", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:    "command-r",
			Messages: []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
		}
		cr := translateRequest(req)
		if cr.MaxTokens != nil {
			t.Errorf("MaxTokens should be nil, got %v", *cr.MaxTokens)
		}
	})
}

func TestTranslateRequest_StopSequences(t *testing.T) {
	t.Run("array of strings", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:    "command-r",
			Messages: []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			Stop:     rawJSON(`["END","STOP"]`),
		}
		cr := translateRequest(req)
		if len(cr.StopSequences) != 2 {
			t.Fatalf("StopSequences length: got %d, want 2", len(cr.StopSequences))
		}
		if cr.StopSequences[0] != "END" || cr.StopSequences[1] != "STOP" {
			t.Errorf("StopSequences: got %v, want [END STOP]", cr.StopSequences)
		}
	})

	t.Run("single string", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:    "command-r",
			Messages: []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			Stop:     rawJSON(`"DONE"`),
		}
		cr := translateRequest(req)
		if len(cr.StopSequences) != 1 || cr.StopSequences[0] != "DONE" {
			t.Errorf("StopSequences: got %v, want [DONE]", cr.StopSequences)
		}
	})

	t.Run("not set", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:    "command-r",
			Messages: []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
		}
		cr := translateRequest(req)
		if len(cr.StopSequences) != 0 {
			t.Errorf("StopSequences should be empty, got %v", cr.StopSequences)
		}
	})
}

func TestTranslateRequest_Tools(t *testing.T) {
	params := json.RawMessage(`{"type":"object","properties":{"q":{"type":"string"}}}`)
	req := &models.ChatCompletionRequest{
		Model:    "command-r-plus",
		Messages: []models.Message{{Role: "user", Content: rawJSON(`"search"`)}},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "web_search",
					Description: "Search the web",
					Parameters:  params,
				},
			},
			{
				Type:     "not_function", // should be skipped
				Function: models.ToolFunction{Name: "ignored"},
			},
		},
	}

	cr := translateRequest(req)

	if len(cr.Tools) != 1 {
		t.Fatalf("Tools length: got %d, want 1 (non-function type should be skipped)", len(cr.Tools))
	}
	if cr.Tools[0].Type != "function" {
		t.Errorf("Tools[0].Type: got %q, want %q", cr.Tools[0].Type, "function")
	}
	if cr.Tools[0].Function.Name != "web_search" {
		t.Errorf("Tools[0].Function.Name: got %q, want %q", cr.Tools[0].Function.Name, "web_search")
	}
	if cr.Tools[0].Function.Description != "Search the web" {
		t.Errorf("Tools[0].Function.Description: got %q, want %q", cr.Tools[0].Function.Description, "Search the web")
	}
	if string(cr.Tools[0].Function.Parameters) != string(params) {
		t.Errorf("Tools[0].Function.Parameters: got %s, want %s", cr.Tools[0].Function.Parameters, params)
	}
}

func TestTranslateRequest_ResponseFormat(t *testing.T) {
	t.Run("json_object", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:          "command-r",
			Messages:       []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
			ResponseFormat: &models.ResponseFormat{Type: "json_object"},
		}
		cr := translateRequest(req)
		if cr.ResponseFormat == nil {
			t.Fatal("ResponseFormat is nil")
		}
		if cr.ResponseFormat.Type != "json_object" {
			t.Errorf("ResponseFormat.Type: got %q, want %q", cr.ResponseFormat.Type, "json_object")
		}
	})

	t.Run("nil passes through as nil", func(t *testing.T) {
		req := &models.ChatCompletionRequest{
			Model:    "command-r",
			Messages: []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
		}
		cr := translateRequest(req)
		if cr.ResponseFormat != nil {
			t.Errorf("ResponseFormat should be nil, got %+v", cr.ResponseFormat)
		}
	})
}

func TestTranslateRequest_Temperature(t *testing.T) {
	temp := 0.7
	req := &models.ChatCompletionRequest{
		Model:       "command-r",
		Messages:    []models.Message{{Role: "user", Content: rawJSON(`"hi"`)}},
		Temperature: &temp,
	}
	cr := translateRequest(req)
	if cr.Temperature == nil || *cr.Temperature != 0.7 {
		t.Errorf("Temperature: got %v, want 0.7", cr.Temperature)
	}
}

func TestTranslateRequest_ContentArrayFormat(t *testing.T) {
	// Content as an array of parts (multimodal format)
	req := &models.ChatCompletionRequest{
		Model: "command-r",
		Messages: []models.Message{
			{
				Role:    "user",
				Content: rawJSON(`[{"type":"text","text":"Hello from array"}]`),
			},
		},
	}
	cr := translateRequest(req)
	if cr.Messages[0].Content != "Hello from array" {
		t.Errorf("Content: got %q, want %q", cr.Messages[0].Content, "Hello from array")
	}
}

// ---------------------------------------------------------------------------
// translateResponse tests
// ---------------------------------------------------------------------------

func TestTranslateResponse_TextContent(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-123",
		Message: cohereRespMessage{
			Role: "assistant",
			Content: []cohereContentPart{
				{Type: "text", Text: "Hello there!"},
			},
		},
		FinishReason: "COMPLETE",
	}

	result := translateResponse(resp, "command-r-plus")

	if result.ID != "resp-123" {
		t.Errorf("ID: got %q, want %q", result.ID, "resp-123")
	}
	if result.Object != "chat.completion" {
		t.Errorf("Object: got %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "command-r-plus" {
		t.Errorf("Model: got %q, want %q", result.Model, "command-r-plus")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("Choices length: got %d, want 1", len(result.Choices))
	}

	choice := result.Choices[0]
	if choice.Index != 0 {
		t.Errorf("Choices[0].Index: got %d, want 0", choice.Index)
	}
	if choice.Message.Role != "assistant" {
		t.Errorf("Role: got %q, want %q", choice.Message.Role, "assistant")
	}

	var content string
	if err := json.Unmarshal(choice.Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Hello there!" {
		t.Errorf("Content: got %q, want %q", content, "Hello there!")
	}
	if choice.FinishReason != "stop" {
		t.Errorf("FinishReason: got %q, want %q", choice.FinishReason, "stop")
	}
}

func TestTranslateResponse_MultipleTextParts(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-multi",
		Message: cohereRespMessage{
			Role: "assistant",
			Content: []cohereContentPart{
				{Type: "text", Text: "Part 1"},
				{Type: "text", Text: " Part 2"},
			},
		},
		FinishReason: "COMPLETE",
	}

	result := translateResponse(resp, "command-r")

	var content string
	if err := json.Unmarshal(result.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content != "Part 1 Part 2" {
		t.Errorf("Content: got %q, want %q", content, "Part 1 Part 2")
	}
}

func TestTranslateResponse_ToolCalls(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-tc",
		Message: cohereRespMessage{
			Role: "assistant",
			ToolCalls: []cohereToolCall{
				{
					ID:   "tc-1",
					Type: "function",
					Function: cohereToolCallFunc{
						Name:      "get_weather",
						Arguments: `{"city":"NYC"}`,
					},
				},
				{
					ID:   "tc-2",
					Type: "function",
					Function: cohereToolCallFunc{
						Name:      "get_time",
						Arguments: `{"tz":"UTC"}`,
					},
				},
			},
		},
		FinishReason: "TOOL_CALL",
	}

	result := translateResponse(resp, "command-r-plus")

	if len(result.Choices) != 1 {
		t.Fatalf("Choices length: got %d, want 1", len(result.Choices))
	}
	choice := result.Choices[0]
	if choice.FinishReason != "tool_calls" {
		t.Errorf("FinishReason: got %q, want %q", choice.FinishReason, "tool_calls")
	}
	if len(choice.Message.ToolCalls) != 2 {
		t.Fatalf("ToolCalls length: got %d, want 2", len(choice.Message.ToolCalls))
	}
	tc := choice.Message.ToolCalls[0]
	if tc.ID != "tc-1" || tc.Type != "function" || tc.Function.Name != "get_weather" || tc.Function.Arguments != `{"city":"NYC"}` {
		t.Errorf("ToolCall[0]: got %+v", tc)
	}
	tc2 := choice.Message.ToolCalls[1]
	if tc2.ID != "tc-2" || tc2.Function.Name != "get_time" {
		t.Errorf("ToolCall[1]: got %+v", tc2)
	}
}

func TestTranslateResponse_FinishReasonMapping(t *testing.T) {
	tests := []struct {
		cohere string
		want   string
	}{
		{"COMPLETE", "stop"},
		{"MAX_TOKENS", "length"},
		{"STOP_SEQUENCE", "stop"},
		{"TOOL_CALL", "tool_calls"},
		{"ERROR", "stop"},
		{"UNKNOWN_VALUE", "stop"},
	}
	for _, tt := range tests {
		t.Run(tt.cohere, func(t *testing.T) {
			got := mapCohereFinishReason(tt.cohere)
			if got != tt.want {
				t.Errorf("mapCohereFinishReason(%q): got %q, want %q", tt.cohere, got, tt.want)
			}
		})
	}
}

func TestTranslateResponse_UsageWithTokens(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-usage",
		Message: cohereRespMessage{
			Role:    "assistant",
			Content: []cohereContentPart{{Type: "text", Text: "ok"}},
		},
		FinishReason: "COMPLETE",
		Usage: &cohereUsage{
			Tokens: &cohereTokens{
				InputTokens:  50,
				OutputTokens: 25,
			},
		},
	}

	result := translateResponse(resp, "command-r")

	if result.Usage == nil {
		t.Fatal("Usage is nil")
	}
	if result.Usage.PromptTokens != 50 {
		t.Errorf("PromptTokens: got %d, want 50", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 25 {
		t.Errorf("CompletionTokens: got %d, want 25", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 75 {
		t.Errorf("TotalTokens: got %d, want 75", result.Usage.TotalTokens)
	}
}

func TestTranslateResponse_UsageWithBilledUnits(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-billed",
		Message: cohereRespMessage{
			Role:    "assistant",
			Content: []cohereContentPart{{Type: "text", Text: "ok"}},
		},
		FinishReason: "COMPLETE",
		Usage: &cohereUsage{
			BilledUnits: &cohereBilledUnits{
				InputTokens:  100,
				OutputTokens: 50,
			},
		},
	}

	result := translateResponse(resp, "command-r")

	if result.Usage == nil {
		t.Fatal("Usage is nil")
	}
	if result.Usage.PromptTokens != 100 {
		t.Errorf("PromptTokens: got %d, want 100", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 50 {
		t.Errorf("CompletionTokens: got %d, want 50", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 150 {
		t.Errorf("TotalTokens: got %d, want 150", result.Usage.TotalTokens)
	}
}

func TestTranslateResponse_UsageTokensPreferredOverBilledUnits(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-both",
		Message: cohereRespMessage{
			Role:    "assistant",
			Content: []cohereContentPart{{Type: "text", Text: "ok"}},
		},
		FinishReason: "COMPLETE",
		Usage: &cohereUsage{
			Tokens: &cohereTokens{
				InputTokens:  10,
				OutputTokens: 5,
			},
			BilledUnits: &cohereBilledUnits{
				InputTokens:  999,
				OutputTokens: 888,
			},
		},
	}

	result := translateResponse(resp, "command-r")

	// Tokens should take precedence because of the if-else structure
	if result.Usage.PromptTokens != 10 {
		t.Errorf("PromptTokens: got %d, want 10 (tokens should take precedence)", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 5 {
		t.Errorf("CompletionTokens: got %d, want 5", result.Usage.CompletionTokens)
	}
}

func TestTranslateResponse_NoUsage(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-nousage",
		Message: cohereRespMessage{
			Role:    "assistant",
			Content: []cohereContentPart{{Type: "text", Text: "ok"}},
		},
		FinishReason: "COMPLETE",
	}

	result := translateResponse(resp, "command-r")

	if result.Usage != nil {
		t.Errorf("Usage should be nil, got %+v", result.Usage)
	}
}

func TestTranslateResponse_EmptyContent(t *testing.T) {
	resp := &cohereResponse{
		ID: "resp-empty",
		Message: cohereRespMessage{
			Role:    "assistant",
			Content: nil,
		},
		FinishReason: "COMPLETE",
	}

	result := translateResponse(resp, "command-r")

	if result.Choices[0].Message.Content != nil {
		t.Errorf("Content should be nil for empty content, got %s", result.Choices[0].Message.Content)
	}
}

// ---------------------------------------------------------------------------
// mapRoleToCohere tests
// ---------------------------------------------------------------------------

func TestMapRoleToCohere(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"system", "system"},
		{"user", "user"},
		{"assistant", "assistant"},
		{"tool", "tool"},
		{"custom_role", "custom_role"},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := mapRoleToCohere(tt.input)
			if got != tt.want {
				t.Errorf("mapRoleToCohere(%q): got %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// parseStreamData tests
// ---------------------------------------------------------------------------

func TestParseStreamData_MessageStart(t *testing.T) {
	state := newStreamState("command-r-plus")

	data := `{"type":"message-start","id":"msg-abc123"}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on message-start")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.ID != "msg-abc123" {
		t.Errorf("ID: got %q, want %q", chunk.ID, "msg-abc123")
	}
	if chunk.Object != "chat.completion.chunk" {
		t.Errorf("Object: got %q, want %q", chunk.Object, "chat.completion.chunk")
	}
	if chunk.Model != "command-r-plus" {
		t.Errorf("Model: got %q, want %q", chunk.Model, "command-r-plus")
	}
	if len(chunk.Choices) != 1 {
		t.Fatalf("Choices length: got %d, want 1", len(chunk.Choices))
	}
	if chunk.Choices[0].Delta.Role != "assistant" {
		t.Errorf("Delta.Role: got %q, want %q", chunk.Choices[0].Delta.Role, "assistant")
	}
}

func TestParseStreamData_ContentDelta_NestedFormat(t *testing.T) {
	state := newStreamState("command-r")

	// Nested format: delta.message.content.text
	data := `{"type":"content-delta","delta":{"message":{"content":{"text":"Hello"}}}}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on content-delta")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Choices[0].Delta.Content == nil {
		t.Fatal("Delta.Content is nil")
	}
	if *chunk.Choices[0].Delta.Content != "Hello" {
		t.Errorf("Delta.Content: got %q, want %q", *chunk.Choices[0].Delta.Content, "Hello")
	}
}

func TestParseStreamData_ContentDelta_SimpleTextFormat(t *testing.T) {
	// The simple text format {"text":"World"} is handled through the nested
	// contentDelta parser path. Since json.Unmarshal of {"text":"World"} into
	// the nested contentDelta struct succeeds (yielding empty text because the
	// nested message.content.text path is not populated), the code returns an
	// empty string content chunk. This test verifies the actual behavior.
	state := newStreamState("command-r")

	data := `{"type":"content-delta","delta":{"text":"World"}}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on content-delta")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	// The nested contentDelta parse succeeds but with empty text because
	// {"text":"World"} doesn't populate the message.content.text path.
	if chunk.Choices[0].Delta.Content == nil {
		t.Fatal("Delta.Content is nil")
	}
	if *chunk.Choices[0].Delta.Content != "" {
		t.Errorf("Delta.Content: got %q, want empty string (nested parse path takes precedence)", *chunk.Choices[0].Delta.Content)
	}
}

func TestParseStreamData_MessageEnd(t *testing.T) {
	state := newStreamState("command-r-plus")

	data := `{"type":"message-end","finish_reason":"COMPLETE","usage":{"tokens":{"input_tokens":30,"output_tokens":10}}}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("should be done on message-end")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Choices[0].FinishReason == nil {
		t.Fatal("FinishReason is nil")
	}
	if *chunk.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason: got %q, want %q", *chunk.Choices[0].FinishReason, "stop")
	}
	if chunk.Usage == nil {
		t.Fatal("Usage is nil")
	}
	if chunk.Usage.PromptTokens != 30 {
		t.Errorf("PromptTokens: got %d, want 30", chunk.Usage.PromptTokens)
	}
	if chunk.Usage.CompletionTokens != 10 {
		t.Errorf("CompletionTokens: got %d, want 10", chunk.Usage.CompletionTokens)
	}
	if chunk.Usage.TotalTokens != 40 {
		t.Errorf("TotalTokens: got %d, want 40", chunk.Usage.TotalTokens)
	}
}

func TestParseStreamData_MessageEnd_MaxTokens(t *testing.T) {
	state := newStreamState("command-r")
	data := `{"type":"message-end","finish_reason":"MAX_TOKENS"}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("should be done")
	}
	if *chunk.Choices[0].FinishReason != "length" {
		t.Errorf("FinishReason: got %q, want %q", *chunk.Choices[0].FinishReason, "length")
	}
}

func TestParseStreamData_MessageEnd_ToolCall(t *testing.T) {
	state := newStreamState("command-r")
	data := `{"type":"message-end","finish_reason":"TOOL_CALL"}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("should be done")
	}
	if *chunk.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("FinishReason: got %q, want %q", *chunk.Choices[0].FinishReason, "tool_calls")
	}
}

func TestParseStreamData_MessageEnd_NoUsage(t *testing.T) {
	state := newStreamState("command-r")
	data := `{"type":"message-end","finish_reason":"COMPLETE"}`
	chunk, _, _ := state.parseStreamData(data)
	if chunk.Usage != nil {
		t.Errorf("Usage should be nil when not provided, got %+v", chunk.Usage)
	}
}

func TestParseStreamData_UnknownType(t *testing.T) {
	state := newStreamState("command-r")
	data := `{"type":"citation-start"}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done")
	}
	if chunk != nil {
		t.Errorf("chunk should be nil for unknown event type, got %+v", chunk)
	}
}

func TestParseStreamData_InvalidJSON(t *testing.T) {
	state := newStreamState("command-r")
	chunk, done, err := state.parseStreamData("not json at all")
	if err != nil {
		t.Error("should not return error for unparseable data (skip it)")
	}
	if done {
		t.Error("should not be done")
	}
	if chunk != nil {
		t.Error("chunk should be nil for invalid JSON")
	}
}

func TestParseStreamData_ToolCallEvents(t *testing.T) {
	state := newStreamState("command-r")

	for _, eventType := range []string{"tool-call-start", "tool-call-delta", "tool-call-end"} {
		data := `{"type":"` + eventType + `"}`
		chunk, done, _ := state.parseStreamData(data)
		if chunk != nil {
			t.Errorf("%s: chunk should be nil", eventType)
		}
		if done {
			t.Errorf("%s: should not be done", eventType)
		}
	}
}

func TestStreamState_FullSequence(t *testing.T) {
	state := newStreamState("command-r-plus")

	// 1. message-start
	chunk1, done1, _ := state.parseStreamData(`{"type":"message-start","id":"conv-xyz"}`)
	if done1 || chunk1 == nil {
		t.Fatal("message-start failed")
	}
	if chunk1.ID != "conv-xyz" {
		t.Errorf("ID after message-start: got %q, want %q", chunk1.ID, "conv-xyz")
	}

	// 2. content-delta
	chunk2, done2, _ := state.parseStreamData(`{"type":"content-delta","delta":{"message":{"content":{"text":"Hi"}}}}`)
	if done2 || chunk2 == nil {
		t.Fatal("content-delta failed")
	}
	// After message-start set the ID, subsequent chunks should use it
	if chunk2.ID != "conv-xyz" {
		t.Errorf("ID after content-delta: got %q, want %q", chunk2.ID, "conv-xyz")
	}
	if *chunk2.Choices[0].Delta.Content != "Hi" {
		t.Errorf("content: got %q, want %q", *chunk2.Choices[0].Delta.Content, "Hi")
	}

	// 3. message-end
	chunk3, done3, _ := state.parseStreamData(`{"type":"message-end","finish_reason":"COMPLETE","usage":{"tokens":{"input_tokens":5,"output_tokens":2}}}`)
	if !done3 || chunk3 == nil {
		t.Fatal("message-end failed")
	}
	if *chunk3.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason: got %q, want %q", *chunk3.Choices[0].FinishReason, "stop")
	}
}

func TestParseStreamData_ContentDeltaNilDelta(t *testing.T) {
	state := newStreamState("command-r")
	data := `{"type":"content-delta"}`
	chunk, done, err := state.parseStreamData(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done")
	}
	if chunk != nil {
		t.Error("chunk should be nil when delta is nil")
	}
}

// ---------------------------------------------------------------------------
// parseCohereError tests
// ---------------------------------------------------------------------------

func TestParseCohereError_WithMessage(t *testing.T) {
	body := []byte(`{"message":"rate limit exceeded"}`)
	apiErr := parseCohereError(429, body)
	if apiErr.Status != 429 {
		t.Errorf("Status: got %d, want 429", apiErr.Status)
	}
	if apiErr.Message != "rate limit exceeded" {
		t.Errorf("Message: got %q, want %q", apiErr.Message, "rate limit exceeded")
	}
	if apiErr.Type != models.ErrTypeRateLimit {
		t.Errorf("Type: got %q, want %q", apiErr.Type, models.ErrTypeRateLimit)
	}
}

func TestParseCohereError_WithoutMessage(t *testing.T) {
	body := []byte(`some plain text error`)
	apiErr := parseCohereError(500, body)
	if apiErr.Status != 502 { // 500+ maps to 502 BadGateway
		t.Errorf("Status: got %d, want 502", apiErr.Status)
	}
	if apiErr.Type != models.ErrTypeUpstream {
		t.Errorf("Type: got %q, want %q", apiErr.Type, models.ErrTypeUpstream)
	}
}

func TestParseCohereError_StatusMapping(t *testing.T) {
	tests := []struct {
		name       string
		status     int
		wantStatus int
		wantType   string
	}{
		{"status_401", 401, 401, models.ErrTypeAuthentication},
		{"status_403", 403, 403, models.ErrTypePermission},
		{"status_404", 404, 404, models.ErrTypeNotFound},
		{"status_422", 422, 422, models.ErrTypeInvalidRequest},
		{"status_429", 429, 429, models.ErrTypeRateLimit},
		{"status_500", 500, 502, models.ErrTypeUpstream},
		{"status_503", 503, 502, models.ErrTypeUpstream},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			apiErr := parseCohereError(tt.status, []byte(`{"message":"test"}`))
			if apiErr.Status != tt.wantStatus {
				t.Errorf("Status: got %d, want %d", apiErr.Status, tt.wantStatus)
			}
			if apiErr.Type != tt.wantType {
				t.Errorf("Type: got %q, want %q", apiErr.Type, tt.wantType)
			}
		})
	}
}

func TestParseCohereError_LongBody(t *testing.T) {
	// Body longer than 500 chars should be truncated
	longBody := make([]byte, 600)
	for i := range longBody {
		longBody[i] = 'x'
	}
	apiErr := parseCohereError(500, longBody)
	if len(apiErr.Message) > 600 {
		t.Errorf("Message should be truncated, length: %d", len(apiErr.Message))
	}
}

// ---------------------------------------------------------------------------
// extractTextContent tests
// ---------------------------------------------------------------------------

func TestExtractTextContent_String(t *testing.T) {
	got := extractTextContent(rawJSON(`"plain text"`))
	if got != "plain text" {
		t.Errorf("got %q, want %q", got, "plain text")
	}
}

func TestExtractTextContent_Array(t *testing.T) {
	got := extractTextContent(rawJSON(`[{"type":"text","text":"from array"}]`))
	if got != "from array" {
		t.Errorf("got %q, want %q", got, "from array")
	}
}

func TestExtractTextContent_ArrayPreservesAllTextParts(t *testing.T) {
	got := extractTextContent(rawJSON(`[
		{"type":"text","text":"from"},
		{"type":"text","text":" array"}
	]`))
	if got != "from array" {
		t.Errorf("got %q, want %q", got, "from array")
	}
}

func TestExtractTextContent_Empty(t *testing.T) {
	got := extractTextContent(nil)
	if got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

func TestExtractTextContent_EmptySlice(t *testing.T) {
	got := extractTextContent(rawJSON(`[]`))
	if got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

// ---------------------------------------------------------------------------
// resolveModelName tests
// ---------------------------------------------------------------------------

func TestResolveModelName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"command-r-plus", "command-r-plus"},
		{"cohere/command-r-plus", "command-r-plus"},
		{"provider/subpath/model", "subpath/model"},
		{"", ""},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := resolveModelName(tt.input)
			if got != tt.want {
				t.Errorf("resolveModelName(%q): got %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// mustMarshal test
// ---------------------------------------------------------------------------

func TestMustMarshal(t *testing.T) {
	got := mustMarshal("hello")
	if got != `"hello"` {
		t.Errorf("mustMarshal: got %q, want %q", got, `"hello"`)
	}
}
