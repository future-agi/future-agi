package anthropic

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func intPtr(v int) *int             { return &v }
func float64Ptr(v float64) *float64 { return &v }

// ---------------------------------------------------------------------------
// translateRequest tests
// ---------------------------------------------------------------------------

func TestTranslateRequest_BasicChat(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-opus-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello, Claude!"`)},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.Model != "claude-3-opus-20240229" {
		t.Errorf("model = %q, want %q", ar.Model, "claude-3-opus-20240229")
	}
	if len(ar.Messages) != 1 {
		t.Fatalf("messages length = %d, want 1", len(ar.Messages))
	}
	if ar.Messages[0].Role != "user" {
		t.Errorf("message role = %q, want %q", ar.Messages[0].Role, "user")
	}

	// Content should be wrapped as a text block array.
	var blocks []anthropicContentBlock
	if err := json.Unmarshal(ar.Messages[0].Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content blocks: %v", err)
	}
	if len(blocks) != 1 || blocks[0].Type != "text" || blocks[0].Text != "Hello, Claude!" {
		t.Errorf("unexpected content blocks: %+v", blocks)
	}
}

func TestTranslateRequest_ResponseFormatJSONSchema(t *testing.T) {
	schema := json.RawMessage(`{"name":"answer","schema":{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"additionalProperties":false},"strict":true}`)
	req := &models.ChatCompletionRequest{
		Model:    "claude-haiku-4-5",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Return JSON"`)}},
		ResponseFormat: &models.ResponseFormat{
			Type:       "json_schema",
			JSONSchema: schema,
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.OutputConfig == nil || ar.OutputConfig.Format == nil {
		t.Fatal("OutputConfig.Format should not be nil")
	}
	if ar.OutputConfig.Format.Type != "json_schema" {
		t.Fatalf("format.type = %q, want %q", ar.OutputConfig.Format.Type, "json_schema")
	}

	var parsed map[string]any
	if err := json.Unmarshal(ar.OutputConfig.Format.Schema, &parsed); err != nil {
		t.Fatalf("failed to unmarshal schema: %v", err)
	}
	if parsed["type"] != "object" {
		t.Fatalf("schema type = %v, want object", parsed["type"])
	}
	if _, ok := parsed["strict"]; ok {
		t.Fatal("schema should not include OpenAI wrapper fields like strict")
	}
}

func TestTranslateRequest_SystemMessageExtraction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are a helpful assistant."`)},
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.System != "You are a helpful assistant." {
		t.Errorf("system = %q, want %q", ar.System, "You are a helpful assistant.")
	}
	// System messages should NOT appear in the messages array.
	if len(ar.Messages) != 1 {
		t.Fatalf("messages length = %d, want 1 (system should be extracted)", len(ar.Messages))
	}
	if ar.Messages[0].Role != "user" {
		t.Errorf("remaining message role = %q, want %q", ar.Messages[0].Role, "user")
	}
}

func TestTranslateRequest_MultipleSystemMessages(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are helpful."`)},
			{Role: "system", Content: json.RawMessage(`"Be concise."`)},
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.System != "You are helpful.\n\nBe concise." {
		t.Errorf("system = %q, want %q", ar.System, "You are helpful.\n\nBe concise.")
	}
	if len(ar.Messages) != 1 {
		t.Fatalf("messages length = %d, want 1", len(ar.Messages))
	}
}

func TestTranslateRequest_MaxTokensDefault(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		// No MaxTokens or MaxCompletionTokens set.
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.MaxTokens != 4096 {
		t.Errorf("max_tokens = %d, want 4096 (default)", ar.MaxTokens)
	}
}

func TestTranslateRequest_MaxTokensExplicit(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxTokens: intPtr(2048),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.MaxTokens != 2048 {
		t.Errorf("max_tokens = %d, want 2048", ar.MaxTokens)
	}
}

func TestTranslateRequest_MaxCompletionTokensFallback(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxCompletionTokens: intPtr(8192),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.MaxTokens != 8192 {
		t.Errorf("max_tokens = %d, want 8192 (from MaxCompletionTokens)", ar.MaxTokens)
	}
}

func TestTranslateRequest_MaxTokensTakesPrecedence(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxTokens:           intPtr(1024),
		MaxCompletionTokens: intPtr(8192),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.MaxTokens != 1024 {
		t.Errorf("max_tokens = %d, want 1024 (MaxTokens takes precedence)", ar.MaxTokens)
	}
}

func TestTranslateRequest_StopSequencesArray(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Stop: json.RawMessage(`["stop1","stop2"]`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(ar.StopSequences) != 2 {
		t.Fatalf("stop_sequences length = %d, want 2", len(ar.StopSequences))
	}
	if ar.StopSequences[0] != "stop1" || ar.StopSequences[1] != "stop2" {
		t.Errorf("stop_sequences = %v, want [stop1 stop2]", ar.StopSequences)
	}
}

func TestTranslateRequest_StopSequencesSingleString(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Stop: json.RawMessage(`"end"`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(ar.StopSequences) != 1 {
		t.Fatalf("stop_sequences length = %d, want 1", len(ar.StopSequences))
	}
	if ar.StopSequences[0] != "end" {
		t.Errorf("stop_sequences[0] = %q, want %q", ar.StopSequences[0], "end")
	}
}

func TestTranslateRequest_TemperatureAndTopP(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Temperature: float64Ptr(0.7),
		TopP:        float64Ptr(0.9),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.Temperature == nil || *ar.Temperature != 0.7 {
		t.Errorf("temperature = %v, want 0.7", ar.Temperature)
	}
	if ar.TopP == nil || *ar.TopP != 0.9 {
		t.Errorf("top_p = %v, want 0.9", ar.TopP)
	}
}

func TestTranslateRequest_ToolsTranslation(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"What is the weather?"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get current weather",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"location":{"type":"string"}}}`),
				},
			},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(ar.Tools) != 1 {
		t.Fatalf("tools length = %d, want 1", len(ar.Tools))
	}
	tool := ar.Tools[0]
	if tool.Name != "get_weather" {
		t.Errorf("tool name = %q, want %q", tool.Name, "get_weather")
	}
	if tool.Description != "Get current weather" {
		t.Errorf("tool description = %q, want %q", tool.Description, "Get current weather")
	}
	if string(tool.InputSchema) != `{"type":"object","properties":{"location":{"type":"string"}}}` {
		t.Errorf("tool input_schema = %s", string(tool.InputSchema))
	}
}

func TestTranslateRequest_ToolsSkipNonFunction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Tools: []models.Tool{
			{Type: "not_a_function", Function: models.ToolFunction{Name: "skip_me"}},
			{Type: "function", Function: models.ToolFunction{Name: "keep_me", Parameters: json.RawMessage(`{}`)}},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(ar.Tools) != 1 {
		t.Fatalf("tools length = %d, want 1 (non-function type should be skipped)", len(ar.Tools))
	}
	if ar.Tools[0].Name != "keep_me" {
		t.Errorf("tool name = %q, want %q", ar.Tools[0].Name, "keep_me")
	}
}

func TestTranslateRequest_ToolChoiceAuto(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"auto"`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.ToolChoice == nil {
		t.Fatal("tool_choice is nil, want auto")
	}
	if ar.ToolChoice.Type != "auto" {
		t.Errorf("tool_choice.type = %q, want %q", ar.ToolChoice.Type, "auto")
	}
}

func TestTranslateRequest_ToolChoiceNone(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"none"`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Anthropic doesn't have "none" -- tool_choice should be nil (omitted).
	if ar.ToolChoice != nil {
		t.Errorf("tool_choice = %+v, want nil (anthropic has no 'none' equivalent)", ar.ToolChoice)
	}
}

func TestTranslateRequest_ToolChoiceRequired(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"required"`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.ToolChoice == nil {
		t.Fatal("tool_choice is nil, want any")
	}
	if ar.ToolChoice.Type != "any" {
		t.Errorf("tool_choice.type = %q, want %q (required maps to any)", ar.ToolChoice.Type, "any")
	}
}

func TestTranslateRequest_ToolChoiceSpecificFunction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`{"type":"function","function":{"name":"my_func"}}`),
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.ToolChoice == nil {
		t.Fatal("tool_choice is nil, want specific function")
	}
	if ar.ToolChoice.Type != "tool" {
		t.Errorf("tool_choice.type = %q, want %q", ar.ToolChoice.Type, "tool")
	}
	if ar.ToolChoice.Name != "my_func" {
		t.Errorf("tool_choice.name = %q, want %q", ar.ToolChoice.Name, "my_func")
	}
}

func TestTranslateRequest_ModelNameWithSlash(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "anthropic/claude-3-sonnet-20240229",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	ar, err := translateRequest(req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ar.Model != "claude-3-sonnet-20240229" {
		t.Errorf("model = %q, want %q (slash prefix should be stripped)", ar.Model, "claude-3-sonnet-20240229")
	}
}

// ---------------------------------------------------------------------------
// translateMessage tests
// ---------------------------------------------------------------------------

func TestTranslateMessage_StandardText(t *testing.T) {
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`"Hello world"`),
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if am.Role != "user" {
		t.Errorf("role = %q, want %q", am.Role, "user")
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 || blocks[0].Type != "text" || blocks[0].Text != "Hello world" {
		t.Errorf("unexpected content blocks: %+v", blocks)
	}
}

func TestTranslateMessage_ToolResultMessage(t *testing.T) {
	msg := models.Message{
		Role:       "tool",
		Content:    json.RawMessage(`"The weather is sunny"`),
		ToolCallID: "toolu_abc",
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if am.Role != "user" {
		t.Errorf("role = %q, want %q", am.Role, "user")
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "tool_result" {
		t.Errorf("block type = %q, want %q", blocks[0].Type, "tool_result")
	}
	if blocks[0].ToolUseID != "toolu_abc" {
		t.Errorf("tool_use_id = %q, want %q", blocks[0].ToolUseID, "toolu_abc")
	}
	// The content field of the tool_result block should contain the text.
	var innerContent []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(blocks[0].Content, &innerContent); err != nil {
		t.Fatalf("failed to unmarshal inner content: %v", err)
	}
	if len(innerContent) != 1 || innerContent[0].Text != "The weather is sunny" {
		t.Errorf("inner content = %+v, want text 'The weather is sunny'", innerContent)
	}
}

func TestTranslateMessage_AssistantWithToolCalls(t *testing.T) {
	msg := models.Message{
		Role:    "assistant",
		Content: json.RawMessage(`"Let me check that."`),
		ToolCalls: []models.ToolCall{
			{
				ID:   "toolu_123",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "get_weather",
					Arguments: `{"location":"NYC"}`,
				},
			},
		},
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if am.Role != "assistant" {
		t.Errorf("role = %q, want %q", am.Role, "assistant")
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	// Should have text block + tool_use block.
	if len(blocks) != 2 {
		t.Fatalf("blocks length = %d, want 2", len(blocks))
	}
	if blocks[0].Type != "text" || blocks[0].Text != "Let me check that." {
		t.Errorf("first block = %+v, want text block", blocks[0])
	}
	if blocks[1].Type != "tool_use" {
		t.Errorf("second block type = %q, want %q", blocks[1].Type, "tool_use")
	}
	if blocks[1].ID != "toolu_123" {
		t.Errorf("tool_use id = %q, want %q", blocks[1].ID, "toolu_123")
	}
	if blocks[1].Name != "get_weather" {
		t.Errorf("tool_use name = %q, want %q", blocks[1].Name, "get_weather")
	}
	if string(blocks[1].Input) != `{"location":"NYC"}` {
		t.Errorf("tool_use input = %s, want %s", string(blocks[1].Input), `{"location":"NYC"}`)
	}
}

func TestTranslateMessage_AssistantToolCallsWithoutText(t *testing.T) {
	msg := models.Message{
		Role: "assistant",
		// Empty content, only tool calls.
		ToolCalls: []models.ToolCall{
			{
				ID:   "toolu_456",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "search",
					Arguments: `{"q":"test"}`,
				},
			},
		},
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	// Only tool_use block, no text.
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "tool_use" {
		t.Errorf("block type = %q, want %q", blocks[0].Type, "tool_use")
	}
}

// ---------------------------------------------------------------------------
// translateResponse tests
// ---------------------------------------------------------------------------

func TestTranslateResponse_TextResponse(t *testing.T) {
	resp := &anthropicResponse{
		ID:    "msg_abc",
		Type:  "message",
		Model: "claude-3-sonnet-20240229",
		Role:  "assistant",
		Content: []anthropicContentBlock{
			{Type: "text", Text: "Hello! How can I help?"},
		},
		StopReason: "end_turn",
		Usage:      anthropicUsage{InputTokens: 10, OutputTokens: 15},
	}

	result := translateResponse(resp)

	if result.ID != "msg_abc" {
		t.Errorf("id = %q, want %q", result.ID, "msg_abc")
	}
	if result.Object != "chat.completion" {
		t.Errorf("object = %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "claude-3-sonnet-20240229" {
		t.Errorf("model = %q, want %q", result.Model, "claude-3-sonnet-20240229")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("choices length = %d, want 1", len(result.Choices))
	}
	choice := result.Choices[0]
	if choice.Index != 0 {
		t.Errorf("choice index = %d, want 0", choice.Index)
	}
	if choice.FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", choice.FinishReason, "stop")
	}
	if choice.Message.Role != "assistant" {
		t.Errorf("message role = %q, want %q", choice.Message.Role, "assistant")
	}

	var content string
	if err := json.Unmarshal(choice.Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Hello! How can I help?" {
		t.Errorf("content = %q, want %q", content, "Hello! How can I help?")
	}
}

func TestTranslateResponse_ToolUseResponse(t *testing.T) {
	resp := &anthropicResponse{
		ID:    "msg_tool",
		Type:  "message",
		Model: "claude-3-sonnet-20240229",
		Role:  "assistant",
		Content: []anthropicContentBlock{
			{
				Type:  "tool_use",
				ID:    "toolu_xyz",
				Name:  "get_weather",
				Input: json.RawMessage(`{"location":"London"}`),
			},
		},
		StopReason: "tool_use",
		Usage:      anthropicUsage{InputTokens: 20, OutputTokens: 30},
	}

	result := translateResponse(resp)

	choice := result.Choices[0]
	if choice.FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", choice.FinishReason, "tool_calls")
	}
	if len(choice.Message.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(choice.Message.ToolCalls))
	}
	tc := choice.Message.ToolCalls[0]
	if tc.ID != "toolu_xyz" {
		t.Errorf("tool call ID = %q, want %q", tc.ID, "toolu_xyz")
	}
	if tc.Type != "function" {
		t.Errorf("tool call type = %q, want %q", tc.Type, "function")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool call name = %q, want %q", tc.Function.Name, "get_weather")
	}
	if tc.Function.Arguments != `{"location":"London"}` {
		t.Errorf("tool call arguments = %q, want %q", tc.Function.Arguments, `{"location":"London"}`)
	}
}

func TestTranslateResponse_FinishReasonMapping(t *testing.T) {
	tests := []struct {
		name       string
		stopReason string
		wantFinish string
	}{
		{"end_turn", "end_turn", "stop"},
		{"max_tokens", "max_tokens", "length"},
		{"stop_sequence", "stop_sequence", "stop"},
		{"tool_use", "tool_use", "tool_calls"},
		{"unknown_reason", "something_else", "stop"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			resp := &anthropicResponse{
				ID:         "msg_fr",
				Model:      "claude-3",
				Role:       "assistant",
				Content:    []anthropicContentBlock{{Type: "text", Text: "ok"}},
				StopReason: tt.stopReason,
				Usage:      anthropicUsage{InputTokens: 1, OutputTokens: 1},
			}
			result := translateResponse(resp)
			if result.Choices[0].FinishReason != tt.wantFinish {
				t.Errorf("finish_reason = %q, want %q", result.Choices[0].FinishReason, tt.wantFinish)
			}
		})
	}
}

func TestTranslateResponse_UsageMapping(t *testing.T) {
	resp := &anthropicResponse{
		ID:         "msg_usage",
		Model:      "claude-3",
		Role:       "assistant",
		Content:    []anthropicContentBlock{{Type: "text", Text: "ok"}},
		StopReason: "end_turn",
		Usage:      anthropicUsage{InputTokens: 100, OutputTokens: 50},
	}

	result := translateResponse(resp)

	if result.Usage == nil {
		t.Fatal("usage is nil")
	}
	if result.Usage.PromptTokens != 100 {
		t.Errorf("prompt_tokens = %d, want 100", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 50 {
		t.Errorf("completion_tokens = %d, want 50", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 150 {
		t.Errorf("total_tokens = %d, want 150", result.Usage.TotalTokens)
	}
}

func TestTranslateResponse_MultipleTextBlocks(t *testing.T) {
	resp := &anthropicResponse{
		ID:    "msg_multi",
		Model: "claude-3",
		Role:  "assistant",
		Content: []anthropicContentBlock{
			{Type: "text", Text: "Hello "},
			{Type: "text", Text: "world!"},
		},
		StopReason: "end_turn",
		Usage:      anthropicUsage{InputTokens: 5, OutputTokens: 5},
	}

	result := translateResponse(resp)

	var content string
	if err := json.Unmarshal(result.Choices[0].Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	// According to the code, multiple text parts are concatenated without separator.
	if content != "Hello world!" {
		t.Errorf("content = %q, want %q", content, "Hello world!")
	}
}

// ---------------------------------------------------------------------------
// parseSSELine tests (from stream.go)
// ---------------------------------------------------------------------------

func TestStreamParse_MessageStart(t *testing.T) {
	state := &streamState{}
	data := `{"type":"message_start","message":{"id":"msg_stream1","model":"claude-3-sonnet-20240229","usage":{"input_tokens":25}}}`

	chunk, done, err := state.parseSSELine("message_start", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false for message_start")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for message_start")
	}
	if chunk.ID != "msg_stream1" {
		t.Errorf("chunk ID = %q, want %q", chunk.ID, "msg_stream1")
	}
	if chunk.Model != "claude-3-sonnet-20240229" {
		t.Errorf("chunk model = %q, want %q", chunk.Model, "claude-3-sonnet-20240229")
	}
	if chunk.Object != "chat.completion.chunk" {
		t.Errorf("chunk object = %q, want %q", chunk.Object, "chat.completion.chunk")
	}
	if len(chunk.Choices) != 1 || chunk.Choices[0].Delta.Role != "assistant" {
		t.Errorf("expected delta with role=assistant, got %+v", chunk.Choices)
	}
	// State should be updated.
	if state.messageID != "msg_stream1" {
		t.Errorf("state messageID = %q, want %q", state.messageID, "msg_stream1")
	}
	if state.inputTokens != 25 {
		t.Errorf("state inputTokens = %d, want 25", state.inputTokens)
	}
}

func TestStreamParse_ContentBlockDelta_TextDelta(t *testing.T) {
	state := &streamState{messageID: "msg_1", model: "claude-3"}
	data := `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}`

	chunk, done, err := state.parseSSELine("content_block_delta", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Choices[0].Delta.Content == nil {
		t.Fatal("delta content should not be nil")
	}
	if *chunk.Choices[0].Delta.Content != "Hello" {
		t.Errorf("delta content = %q, want %q", *chunk.Choices[0].Delta.Content, "Hello")
	}
	if chunk.ID != "msg_1" {
		t.Errorf("chunk ID = %q, want %q (from state)", chunk.ID, "msg_1")
	}
}

func TestStreamParse_ContentBlockDelta_InputJSONDelta(t *testing.T) {
	state := &streamState{messageID: "msg_2", model: "claude-3"}
	data := `{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"loc"}}`

	chunk, done, err := state.parseSSELine("content_block_delta", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if len(chunk.Choices[0].Delta.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(chunk.Choices[0].Delta.ToolCalls))
	}
	tc := chunk.Choices[0].Delta.ToolCalls[0]
	// Anthropic block index is 1 (text=0, tool=1), but OpenAI tool_calls index
	// is 0-based among tool calls only. The first tool call → index 0.
	if tc.Index != 0 {
		t.Errorf("tool call index = %d, want 0", tc.Index)
	}
	if tc.Function == nil || tc.Function.Arguments != `{"loc` {
		t.Errorf("tool call args = %v, want %q", tc.Function, `{"loc`)
	}
}

func TestStreamParse_MessageDelta_StopReason(t *testing.T) {
	state := &streamState{messageID: "msg_3", model: "claude-3", inputTokens: 10}
	data := `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":20}}`

	chunk, done, err := state.parseSSELine("message_delta", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false for message_delta")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Choices[0].FinishReason == nil {
		t.Fatal("finish_reason should not be nil")
	}
	if *chunk.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", *chunk.Choices[0].FinishReason, "stop")
	}
	// Check usage.
	if chunk.Usage == nil {
		t.Fatal("usage should not be nil")
	}
	if chunk.Usage.PromptTokens != 10 {
		t.Errorf("prompt_tokens = %d, want 10", chunk.Usage.PromptTokens)
	}
	if chunk.Usage.CompletionTokens != 20 {
		t.Errorf("completion_tokens = %d, want 20", chunk.Usage.CompletionTokens)
	}
	if chunk.Usage.TotalTokens != 30 {
		t.Errorf("total_tokens = %d, want 30", chunk.Usage.TotalTokens)
	}
}

func TestStreamParse_MessageDelta_ToolUseStopReason(t *testing.T) {
	state := &streamState{messageID: "msg_4", model: "claude-3", inputTokens: 5}
	data := `{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":15}}`

	chunk, done, err := state.parseSSELine("message_delta", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if *chunk.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", *chunk.Choices[0].FinishReason, "tool_calls")
	}
}

func TestStreamParse_MessageStop(t *testing.T) {
	state := &streamState{messageID: "msg_5"}
	data := `{"type":"message_stop"}`

	chunk, done, err := state.parseSSELine("message_stop", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("done should be true for message_stop")
	}
	if chunk != nil {
		t.Error("chunk should be nil for message_stop")
	}
}

func TestStreamParse_Ping(t *testing.T) {
	state := &streamState{}
	data := `{"type":"ping"}`

	chunk, done, err := state.parseSSELine("ping", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false for ping")
	}
	if chunk != nil {
		t.Error("chunk should be nil for ping (no data to emit)")
	}
}

func TestStreamParse_ContentBlockStop(t *testing.T) {
	state := &streamState{}
	data := `{"type":"content_block_stop","index":0}`

	chunk, done, err := state.parseSSELine("content_block_stop", data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk != nil {
		t.Error("chunk should be nil for content_block_stop")
	}
}

func TestStreamParse_EmptyData(t *testing.T) {
	state := &streamState{}

	chunk, done, err := state.parseSSELine("", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false for empty data")
	}
	if chunk != nil {
		t.Error("chunk should be nil for empty data")
	}
}

func TestStreamParse_InvalidJSON(t *testing.T) {
	state := &streamState{}
	data := `{not valid json`

	chunk, done, err := state.parseSSELine("content_block_delta", data)
	if err != nil {
		t.Fatalf("unexpected error: %v (should be silently ignored)", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk != nil {
		t.Error("chunk should be nil for invalid JSON")
	}
}

// ---------------------------------------------------------------------------
// parseSSELines tests
// ---------------------------------------------------------------------------

func TestParseSSELines_FullSequence(t *testing.T) {
	lines := []string{
		"event: message_start",
		`data: {"type":"message_start","message":{"id":"msg_seq","model":"claude-3","usage":{"input_tokens":10}}}`,
		"",
		"event: content_block_start",
		`data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
		"",
		"event: ping",
		`data: {"type":"ping"}`,
		"",
		"event: content_block_delta",
		`data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}`,
		"",
		"event: content_block_delta",
		`data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" there"}}`,
		"",
		"event: content_block_stop",
		`data: {"type":"content_block_stop","index":0}`,
		"",
		"event: message_delta",
		`data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}`,
		"",
		"event: message_stop",
		`data: {"type":"message_stop"}`,
		"",
	}

	state := &streamState{}
	chunks, done, err := parseSSELines(lines, state)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("expected done=true after message_stop")
	}

	// Expected chunks: message_start, text_delta "Hi", text_delta " there", message_delta
	// (content_block_start, ping, content_block_stop produce no chunks)
	if len(chunks) != 4 {
		t.Fatalf("chunks count = %d, want 4, got chunks: %+v", len(chunks), chunks)
	}

	// First chunk: message_start with role.
	if chunks[0].Choices[0].Delta.Role != "assistant" {
		t.Errorf("chunk[0] role = %q, want %q", chunks[0].Choices[0].Delta.Role, "assistant")
	}

	// Second chunk: text "Hi".
	if chunks[1].Choices[0].Delta.Content == nil || *chunks[1].Choices[0].Delta.Content != "Hi" {
		t.Errorf("chunk[1] content = %v, want %q", chunks[1].Choices[0].Delta.Content, "Hi")
	}

	// Third chunk: text " there".
	if chunks[2].Choices[0].Delta.Content == nil || *chunks[2].Choices[0].Delta.Content != " there" {
		t.Errorf("chunk[2] content = %v, want %q", chunks[2].Choices[0].Delta.Content, " there")
	}

	// Fourth chunk: message_delta with finish_reason.
	if chunks[3].Choices[0].FinishReason == nil || *chunks[3].Choices[0].FinishReason != "stop" {
		t.Errorf("chunk[3] finish_reason = %v, want %q", chunks[3].Choices[0].FinishReason, "stop")
	}
}

// ---------------------------------------------------------------------------
// parseAnthropicError tests
// ---------------------------------------------------------------------------

func TestParseAnthropicError_ValidJSON(t *testing.T) {
	tests := []struct {
		name       string
		status     int
		errorType  string
		message    string
		wantStatus int
		wantType   string
	}{
		{"invalid_request", 400, "invalid_request_error", "Bad request.", 400, models.ErrTypeInvalidRequest},
		{"authentication", 401, "authentication_error", "Invalid API key.", 401, models.ErrTypeAuthentication},
		{"permission", 403, "permission_error", "Not allowed.", 403, models.ErrTypePermission},
		{"not_found", 404, "not_found_error", "Model not found.", 404, models.ErrTypeNotFound},
		{"rate_limit", 429, "rate_limit_error", "Rate limited.", 429, models.ErrTypeRateLimit},
		{"overloaded", 529, "overloaded_error", "Overloaded.", 502, models.ErrTypeUpstream},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body, _ := json.Marshal(anthropicErrorResponse{
				Type: "error",
				Error: anthropicErrorDetail{
					Type:    tt.errorType,
					Message: tt.message,
				},
			})
			apiErr := parseAnthropicError(tt.status, body)
			if apiErr.Status != tt.wantStatus {
				t.Errorf("status = %d, want %d", apiErr.Status, tt.wantStatus)
			}
			if apiErr.Type != tt.wantType {
				t.Errorf("type = %q, want %q", apiErr.Type, tt.wantType)
			}
			if apiErr.Message != tt.message {
				t.Errorf("message = %q, want %q", apiErr.Message, tt.message)
			}
			wantCode := "provider_" + tt.errorType
			if apiErr.Code != wantCode {
				t.Errorf("code = %q, want %q", apiErr.Code, wantCode)
			}
		})
	}
}

func TestParseAnthropicError_InvalidJSON(t *testing.T) {
	apiErr := parseAnthropicError(500, []byte("not json"))
	if apiErr.Status != 502 {
		t.Errorf("status = %d, want 502", apiErr.Status)
	}
	if !strings.Contains(apiErr.Message, "not json") {
		t.Errorf("message = %q, want it to contain the raw body", apiErr.Message)
	}
}

func TestParseAnthropicError_TruncatesLongBody(t *testing.T) {
	longBody := strings.Repeat("x", 600)
	apiErr := parseAnthropicError(500, []byte(longBody))
	if !strings.HasSuffix(apiErr.Message, "...") {
		t.Error("expected message to be truncated with '...'")
	}
}

// ---------------------------------------------------------------------------
// translateVisionContent tests
// ---------------------------------------------------------------------------

func TestTranslateVisionContent_WithImages(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"What is in this image?"},
		{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
	]`)

	blocks := translateVisionContent(content)
	if blocks == nil {
		t.Fatal("expected non-nil blocks for content with images")
	}
	if len(blocks) != 2 {
		t.Fatalf("blocks length = %d, want 2", len(blocks))
	}

	// First block: text.
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "What is in this image?" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "What is in this image?")
	}

	// Second block: image with base64 source.
	if blocks[1].Type != "image" {
		t.Errorf("blocks[1].Type = %q, want %q", blocks[1].Type, "image")
	}
	if blocks[1].Source == nil {
		t.Fatal("blocks[1].Source should not be nil")
	}
	if blocks[1].Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", blocks[1].Source.Type, "base64")
	}
	if blocks[1].Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", blocks[1].Source.MediaType, "image/png")
	}
	if blocks[1].Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", blocks[1].Source.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateVisionContent_NoImages(t *testing.T) {
	// Content array with only text parts should return nil (falls through to text extraction).
	content := json.RawMessage(`[{"type":"text","text":"Just text, no images"}]`)

	blocks := translateVisionContent(content)
	if blocks != nil {
		t.Errorf("expected nil for text-only content array, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_DataURI(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,/9j/4AAQSkZJRg=="}}
	]`)

	blocks := translateVisionContent(content)
	if blocks == nil {
		t.Fatal("expected non-nil blocks")
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "image" {
		t.Errorf("Type = %q, want %q", blocks[0].Type, "image")
	}
	if blocks[0].Source == nil {
		t.Fatal("Source should not be nil")
	}
	if blocks[0].Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "base64")
	}
	if blocks[0].Source.MediaType != "image/jpeg" {
		t.Errorf("Source.MediaType = %q, want %q", blocks[0].Source.MediaType, "image/jpeg")
	}
	if blocks[0].Source.Data != "/9j/4AAQSkZJRg==" {
		t.Errorf("Source.Data = %q, want %q", blocks[0].Source.Data, "/9j/4AAQSkZJRg==")
	}
}

func TestTranslateVisionContent_HTTPURL(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}
	]`)

	blocks := translateVisionContent(content)
	if blocks == nil {
		t.Fatal("expected non-nil blocks")
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "image" {
		t.Errorf("Type = %q, want %q", blocks[0].Type, "image")
	}
	if blocks[0].Source == nil {
		t.Fatal("Source should not be nil")
	}
	if blocks[0].Source.Type != "url" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "url")
	}
	if blocks[0].Source.URL != "https://example.com/image.png" {
		t.Errorf("Source.URL = %q, want %q", blocks[0].Source.URL, "https://example.com/image.png")
	}
}

func TestTranslateVisionContent_EmptyContent(t *testing.T) {
	blocks := translateVisionContent(nil)
	if blocks != nil {
		t.Errorf("expected nil for nil content, got %d blocks", len(blocks))
	}

	blocks = translateVisionContent(json.RawMessage(""))
	if blocks != nil {
		t.Errorf("expected nil for empty content, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_InvalidJSON(t *testing.T) {
	blocks := translateVisionContent(json.RawMessage(`{not valid`))
	if blocks != nil {
		t.Errorf("expected nil for invalid JSON, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_NilImageURL(t *testing.T) {
	// image_url part with null image_url field should be skipped.
	content := json.RawMessage(`[
		{"type":"text","text":"Hello"},
		{"type":"image_url","image_url":null}
	]`)

	// No valid image_url parts, so should return nil (hasImage is false).
	blocks := translateVisionContent(content)
	if blocks != nil {
		t.Errorf("expected nil for content with null image_url, got %d blocks", len(blocks))
	}
}

// ---------------------------------------------------------------------------
// convertImageToAnthropic tests
// ---------------------------------------------------------------------------

func TestConvertImageToAnthropic_DataURI(t *testing.T) {
	block := convertImageToAnthropic("data:image/png;base64,iVBORw0KGgo=")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Type != "image" {
		t.Errorf("Type = %q, want %q", block.Type, "image")
	}
	if block.Source == nil {
		t.Fatal("Source should not be nil")
	}
	if block.Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", block.Source.Type, "base64")
	}
	if block.Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", block.Source.MediaType, "image/png")
	}
	if block.Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", block.Source.Data, "iVBORw0KGgo=")
	}
}

func TestConvertImageToAnthropic_DataURI_JPEG(t *testing.T) {
	block := convertImageToAnthropic("data:image/jpeg;base64,/9j/4AAQSkZJRg==")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Source.MediaType != "image/jpeg" {
		t.Errorf("Source.MediaType = %q, want %q", block.Source.MediaType, "image/jpeg")
	}
	if block.Source.Data != "/9j/4AAQSkZJRg==" {
		t.Errorf("Source.Data = %q, want %q", block.Source.Data, "/9j/4AAQSkZJRg==")
	}
}

func TestConvertImageToAnthropic_HTTPURL(t *testing.T) {
	block := convertImageToAnthropic("https://example.com/photo.jpg")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Type != "image" {
		t.Errorf("Type = %q, want %q", block.Type, "image")
	}
	if block.Source == nil {
		t.Fatal("Source should not be nil")
	}
	if block.Source.Type != "url" {
		t.Errorf("Source.Type = %q, want %q", block.Source.Type, "url")
	}
	if block.Source.URL != "https://example.com/photo.jpg" {
		t.Errorf("Source.URL = %q, want %q", block.Source.URL, "https://example.com/photo.jpg")
	}
}

func TestConvertImageToAnthropic_InvalidDataURI(t *testing.T) {
	// No semicolon in data URI -> parseDataURI returns empty data -> nil block.
	block := convertImageToAnthropic("data:image/png")
	if block != nil {
		t.Errorf("expected nil for invalid data URI without semicolon, got %+v", block)
	}
}

// ---------------------------------------------------------------------------
// parseDataURI tests
// ---------------------------------------------------------------------------

func TestParseDataURI_PNG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png;base64,iVBORw0KGgo=")
	if mediaType != "image/png" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/png")
	}
	if data != "iVBORw0KGgo=" {
		t.Errorf("data = %q, want %q", data, "iVBORw0KGgo=")
	}
}

func TestParseDataURI_JPEG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/jpeg;base64,/9j/4AAQ")
	if mediaType != "image/jpeg" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/jpeg")
	}
	if data != "/9j/4AAQ" {
		t.Errorf("data = %q, want %q", data, "/9j/4AAQ")
	}
}

func TestParseDataURI_WebP(t *testing.T) {
	mediaType, data := parseDataURI("data:image/webp;base64,UklGRl4=")
	if mediaType != "image/webp" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/webp")
	}
	if data != "UklGRl4=" {
		t.Errorf("data = %q, want %q", data, "UklGRl4=")
	}
}

func TestParseDataURI_NoSemicolon(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png")
	if mediaType != "" {
		t.Errorf("mediaType = %q, want empty", mediaType)
	}
	if data != "" {
		t.Errorf("data = %q, want empty", data)
	}
}

func TestParseDataURI_NoBase64Prefix(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png;charset=utf-8,notbase64")
	if mediaType != "image/png" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/png")
	}
	// No "base64," prefix, so data should be empty.
	if data != "" {
		t.Errorf("data = %q, want empty (no base64 prefix)", data)
	}
}

func TestParseDataURI_EmptyData(t *testing.T) {
	mediaType, data := parseDataURI("data:image/gif;base64,")
	if mediaType != "image/gif" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/gif")
	}
	if data != "" {
		t.Errorf("data = %q, want empty", data)
	}
}

// ---------------------------------------------------------------------------
// translateMessage with vision content tests
// ---------------------------------------------------------------------------

func TestTranslateMessage_VisionContent_ImageOnly(t *testing.T) {
	// When content has only image_url parts (no text), extractTextContent returns ""
	// and the vision path is taken via translateVisionContent.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if am.Role != "user" {
		t.Errorf("role = %q, want %q", am.Role, "user")
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "image" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "image")
	}
	if blocks[0].Source == nil {
		t.Fatal("blocks[0].Source should not be nil")
	}
	if blocks[0].Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "base64")
	}
	if blocks[0].Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", blocks[0].Source.MediaType, "image/png")
	}
	if blocks[0].Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", blocks[0].Source.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateMessage_VisionContent_WithText(t *testing.T) {
	// When content has both text and image_url parts, the vision path handles both.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"Describe this image"},
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if am.Role != "user" {
		t.Errorf("role = %q, want %q", am.Role, "user")
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	// Vision path preserves both text and image parts.
	if len(blocks) != 2 {
		t.Fatalf("blocks length = %d, want 2 (text + image)", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Describe this image" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "Describe this image")
	}
	if blocks[1].Type != "image" {
		t.Errorf("blocks[1].Type = %q, want %q", blocks[1].Type, "image")
	}
	if blocks[1].Source == nil {
		t.Fatal("blocks[1].Source is nil, want non-nil")
	}
	if blocks[1].Source.Type != "base64" {
		t.Errorf("blocks[1].Source.Type = %q, want %q", blocks[1].Source.Type, "base64")
	}
}

func TestTranslateMessage_VisionContent_HTTPUrl_ImageOnly(t *testing.T) {
	// Image-only content with HTTP URL (no text parts).
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"image_url","image_url":{"url":"https://example.com/img.jpg"}}
		]`),
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Source.Type != "url" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "url")
	}
	if blocks[0].Source.URL != "https://example.com/img.jpg" {
		t.Errorf("Source.URL = %q, want %q", blocks[0].Source.URL, "https://example.com/img.jpg")
	}
}

func TestTranslateMessage_VisionContent_TextOnlyFallback(t *testing.T) {
	// When content is a text-only array (no image_url parts), translateMessage
	// should fall through to extractTextContent, not translateVisionContent.
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`[{"type":"text","text":"Just text"}]`),
	}

	am, err := translateMessage(msg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// extractTextContent extracts "Just text" -> wrapped in text block.
	var blocks []anthropicContentBlock
	if err := json.Unmarshal(am.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Just text" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "Just text")
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
		{"claude-3-sonnet-20240229", "claude-3-sonnet-20240229"},
		{"anthropic/claude-3-sonnet-20240229", "claude-3-sonnet-20240229"},
		{"provider/model/variant", "model/variant"},
		{"", ""},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := resolveModelName(tt.input)
			if got != tt.want {
				t.Errorf("resolveModelName(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// extractTextContent tests
// ---------------------------------------------------------------------------

func TestExtractTextContent(t *testing.T) {
	tests := []struct {
		name    string
		content json.RawMessage
		want    string
	}{
		{"empty", nil, ""},
		{"string", json.RawMessage(`"Hello"`), "Hello"},
		{"array_with_text", json.RawMessage(`[{"type":"text","text":"Hi there"}]`), "Hi there"},
		{"array_without_text_type", json.RawMessage(`[{"type":"image","url":"x"}]`), ""},
		{"invalid_json", json.RawMessage(`{invalid`), ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractTextContent(tt.content)
			if got != tt.want {
				t.Errorf("extractTextContent() = %q, want %q", got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// mapStopReason tests
// ---------------------------------------------------------------------------

func TestMapStopReason(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"end_turn", "stop"},
		{"max_tokens", "length"},
		{"stop_sequence", "stop"},
		{"tool_use", "tool_calls"},
		{"", "stop"},
		{"something_unknown", "stop"},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := mapStopReason(tt.input)
			if got != tt.want {
				t.Errorf("mapStopReason(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}
