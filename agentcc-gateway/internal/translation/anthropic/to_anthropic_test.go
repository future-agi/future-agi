package anthropic_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

// ─── helpers ──────────────────────────────────────────────────────────────────

func makeStringPtr(s string) *string { return &s }

func intPtr(i int) *int { return &i }

// decodeResponse decodes the raw response JSON into a map for assertions.
func decodeResponse(t *testing.T, body []byte) map[string]interface{} {
	t.Helper()
	var m map[string]interface{}
	if err := json.Unmarshal(body, &m); err != nil {
		t.Fatalf("cannot unmarshal response: %v\nbody: %s", err, body)
	}
	return m
}

// ─── Simple text response ─────────────────────────────────────────────────────

func TestResponseFromCanonical_SimpleText(t *testing.T) {
	content, _ := json.Marshal("Hello from Claude!")
	resp := &models.ChatCompletionResponse{
		ID:    "chatcmpl-abc123",
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message:      models.Message{Role: "assistant", Content: content},
				FinishReason: "stop",
			},
		},
		Usage: &models.Usage{
			PromptTokens:     10,
			CompletionTokens: 5,
		},
	}

	body, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	m := decodeResponse(t, body)

	if m["type"] != "message" {
		t.Errorf("expected type=message, got %v", m["type"])
	}
	if m["role"] != "assistant" {
		t.Errorf("expected role=assistant, got %v", m["role"])
	}
	if m["stop_reason"] != "end_turn" {
		t.Errorf("expected stop_reason=end_turn, got %v", m["stop_reason"])
	}

	// Check content blocks.
	contentBlocks := m["content"].([]interface{})
	if len(contentBlocks) != 1 {
		t.Fatalf("expected 1 content block, got %d", len(contentBlocks))
	}
	block := contentBlocks[0].(map[string]interface{})
	if block["type"] != "text" {
		t.Errorf("expected block type=text, got %v", block["type"])
	}
	if block["text"] != "Hello from Claude!" {
		t.Errorf("expected text='Hello from Claude!', got %v", block["text"])
	}

	// Check usage.
	usage := m["usage"].(map[string]interface{})
	if usage["input_tokens"].(float64) != 10 {
		t.Errorf("expected input_tokens=10, got %v", usage["input_tokens"])
	}
	if usage["output_tokens"].(float64) != 5 {
		t.Errorf("expected output_tokens=5, got %v", usage["output_tokens"])
	}
}

// ─── Tool use response ────────────────────────────────────────────────────────

func TestResponseFromCanonical_ToolUse(t *testing.T) {
	content, _ := json.Marshal("I'll check the weather.")
	resp := &models.ChatCompletionResponse{
		ID:    "chatcmpl-tool1",
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message: models.Message{
					Role:    "assistant",
					Content: content,
					ToolCalls: []models.ToolCall{
						{
							ID:   "toolu_01",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "get_weather",
								Arguments: `{"city":"London"}`,
							},
						},
					},
				},
				FinishReason: "tool_calls",
			},
		},
		Usage: &models.Usage{PromptTokens: 20, CompletionTokens: 15},
	}

	body, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	m := decodeResponse(t, body)

	if m["stop_reason"] != "tool_use" {
		t.Errorf("expected stop_reason=tool_use, got %v", m["stop_reason"])
	}

	contentBlocks := m["content"].([]interface{})
	if len(contentBlocks) != 2 {
		t.Fatalf("expected 2 content blocks (text + tool_use), got %d: %v", len(contentBlocks), contentBlocks)
	}

	// First block: text.
	textBlock := contentBlocks[0].(map[string]interface{})
	if textBlock["type"] != "text" {
		t.Errorf("first block should be text, got %v", textBlock["type"])
	}

	// Second block: tool_use.
	toolBlock := contentBlocks[1].(map[string]interface{})
	if toolBlock["type"] != "tool_use" {
		t.Errorf("second block should be tool_use, got %v", toolBlock["type"])
	}
	if toolBlock["id"] != "toolu_01" {
		t.Errorf("expected id=toolu_01, got %v", toolBlock["id"])
	}
	if toolBlock["name"] != "get_weather" {
		t.Errorf("expected name=get_weather, got %v", toolBlock["name"])
	}

	// Check that input is a JSON object.
	input := toolBlock["input"].(map[string]interface{})
	if input["city"] != "London" {
		t.Errorf("expected input.city=London, got %v", input["city"])
	}
}

// ─── Tool name restoration ─────────────────────────────────────────────────────

func TestResponseFromCanonical_ToolNameRestoration(t *testing.T) {
	shortName := strings.Repeat("a", 64)
	origName := strings.Repeat("a", 70)
	toolNameMapping := map[string]string{shortName: origName}

	resp := &models.ChatCompletionResponse{
		ID:    "chatcmpl-toolrestore",
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message: models.Message{
					Role: "assistant",
					ToolCalls: []models.ToolCall{
						{
							ID:   "toolu_01",
							Type: "function",
							Function: models.FunctionCall{
								Name:      shortName,
								Arguments: `{}`,
							},
						},
					},
				},
				FinishReason: "tool_calls",
			},
		},
	}

	body, err := tr.ResponseFromCanonicalWithMapping(resp, toolNameMapping)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	m := decodeResponse(t, body)
	contentBlocks := m["content"].([]interface{})
	toolBlock := contentBlocks[0].(map[string]interface{})
	if toolBlock["name"] != origName {
		t.Errorf("expected restored name=%q, got %v", origName, toolBlock["name"])
	}
}

// ─── finish_reason mapping ────────────────────────────────────────────────────

func TestResponseFromCanonical_FinishReasonMapping(t *testing.T) {
	tests := []struct {
		openaiReason    string
		anthropicReason string
	}{
		{"stop", "end_turn"},
		{"length", "max_tokens"},
		{"tool_calls", "tool_use"},
		{"content_filter", "stop_sequence"},
		{"", "end_turn"},
	}

	for _, tc := range tests {
		t.Run(tc.openaiReason, func(t *testing.T) {
			content, _ := json.Marshal("text")
			resp := &models.ChatCompletionResponse{
				ID:    "chatcmpl-test",
				Model: "claude-3-5-sonnet-20241022",
				Choices: []models.Choice{
					{
						Message:      models.Message{Role: "assistant", Content: content},
						FinishReason: tc.openaiReason,
					},
				},
			}

			body, err := tr.ResponseFromCanonical(resp)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			m := decodeResponse(t, body)
			if m["stop_reason"] != tc.anthropicReason {
				t.Errorf("finish_reason=%q: expected stop_reason=%q, got %q",
					tc.openaiReason, tc.anthropicReason, m["stop_reason"])
			}
		})
	}
}

// ─── Empty choices error ──────────────────────────────────────────────────────

func TestResponseFromCanonical_EmptyChoices(t *testing.T) {
	resp := &models.ChatCompletionResponse{
		ID:      "chatcmpl-empty",
		Choices: []models.Choice{},
	}
	_, err := tr.ResponseFromCanonical(resp)
	if err == nil {
		t.Error("expected error for empty choices")
	}
}

// ─── Generated ID ─────────────────────────────────────────────────────────────

func TestResponseFromCanonical_GeneratedID(t *testing.T) {
	content, _ := json.Marshal("hi")
	resp := &models.ChatCompletionResponse{
		ID:    "", // no ID
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message:      models.Message{Role: "assistant", Content: content},
				FinishReason: "stop",
			},
		},
	}

	body, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	m := decodeResponse(t, body)
	id, _ := m["id"].(string)
	if !strings.HasPrefix(id, "msg_") {
		t.Errorf("expected generated msg_* ID, got %q", id)
	}
}

// ─── Parallel tool calls response ─────────────────────────────────────────────

func TestResponseFromCanonical_ParallelToolCalls(t *testing.T) {
	resp := &models.ChatCompletionResponse{
		ID:    "chatcmpl-parallel",
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message: models.Message{
					Role: "assistant",
					ToolCalls: []models.ToolCall{
						{ID: "toolu_01", Type: "function", Function: models.FunctionCall{Name: "tool_a", Arguments: `{"x":1}`}},
						{ID: "toolu_02", Type: "function", Function: models.FunctionCall{Name: "tool_b", Arguments: `{"y":2}`}},
					},
				},
				FinishReason: "tool_calls",
			},
		},
	}

	body, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	m := decodeResponse(t, body)
	blocks := m["content"].([]interface{})
	if len(blocks) != 2 {
		t.Errorf("expected 2 tool_use blocks, got %d", len(blocks))
	}
	for i, b := range blocks {
		block := b.(map[string]interface{})
		if block["type"] != "tool_use" {
			t.Errorf("block[%d]: expected type=tool_use, got %v", i, block["type"])
		}
	}
}

// ─── ResponseFromCanonicalWithMapping is accessible ──────────────────────────

func TestResponseFromCanonicalWithMapping_Empty(t *testing.T) {
	content, _ := json.Marshal("ok")
	resp := &models.ChatCompletionResponse{
		ID:    "chatcmpl-withmap",
		Model: "claude-3-5-sonnet-20241022",
		Choices: []models.Choice{
			{
				Message:      models.Message{Role: "assistant", Content: content},
				FinishReason: "stop",
			},
		},
	}

	tr2 := anthropic.New()
	body, err := tr2.ResponseFromCanonicalWithMapping(resp, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	m := decodeResponse(t, body)
	if m["stop_reason"] != "end_turn" {
		t.Errorf("expected end_turn, got %v", m["stop_reason"])
	}
}
