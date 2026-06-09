package tokenizer

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestEstimateTokens_SingleMessage(t *testing.T) {
	content, _ := json.Marshal("Hello, how are you?")
	messages := []models.Message{
		{Role: "user", Content: content},
	}

	tokens := EstimateTokens(messages)

	// Should be > 0 and reasonable.
	if tokens <= 0 {
		t.Errorf("EstimateTokens returned %d, want > 0", tokens)
	}
	// "Hello, how are you?" is 5 words. ~6.5 word tokens + 3 msg overhead + 1 role + 3 reply = ~13-14 tokens.
	// The actual cl100k_base count is ~7 tokens, but our estimator adds overhead.
	if tokens > 30 {
		t.Errorf("EstimateTokens returned %d, seems too high for a short message", tokens)
	}
}

func TestEstimateTokens_MultipleMessages(t *testing.T) {
	systemContent, _ := json.Marshal("You are a helpful assistant.")
	userContent, _ := json.Marshal("What is the capital of France?")
	messages := []models.Message{
		{Role: "system", Content: systemContent},
		{Role: "user", Content: userContent},
	}

	tokens := EstimateTokens(messages)

	if tokens <= 0 {
		t.Errorf("EstimateTokens returned %d, want > 0", tokens)
	}
	// Two messages, each with ~6-7 content tokens + overhead = ~25-30 total.
	if tokens > 50 {
		t.Errorf("EstimateTokens returned %d, seems too high", tokens)
	}
}

func TestEstimateTokens_EmptyMessages(t *testing.T) {
	messages := []models.Message{}

	tokens := EstimateTokens(messages)

	// Should be 3 (reply primer only).
	if tokens != 3 {
		t.Errorf("EstimateTokens with empty messages = %d, want 3", tokens)
	}
}

func TestEstimateTokens_LongContent(t *testing.T) {
	// ~500 words of content.
	long := ""
	for i := 0; i < 500; i++ {
		long += "word "
	}
	content, _ := json.Marshal(long)
	messages := []models.Message{
		{Role: "user", Content: content},
	}

	tokens := EstimateTokens(messages)

	// 500 words * 1.3 tokens/word = ~650 content tokens + overhead.
	if tokens < 500 {
		t.Errorf("EstimateTokens returned %d for 500 words, want >= 500", tokens)
	}
	if tokens > 900 {
		t.Errorf("EstimateTokens returned %d for 500 words, seems too high", tokens)
	}
}

func TestEstimateTokens_WithToolCalls(t *testing.T) {
	content, _ := json.Marshal("Use the weather tool")
	messages := []models.Message{
		{Role: "user", Content: content},
		{
			Role:    "assistant",
			Content: nil,
			ToolCalls: []models.ToolCall{
				{
					ID:   "call_1",
					Type: "function",
					Function: models.FunctionCall{
						Name:      "get_weather",
						Arguments: `{"location": "San Francisco"}`,
					},
				},
			},
		},
	}

	tokens := EstimateTokens(messages)

	if tokens <= 0 {
		t.Errorf("EstimateTokens returned %d, want > 0", tokens)
	}
}

func TestEstimateTokens_WithName(t *testing.T) {
	content, _ := json.Marshal("Hello")
	messages := []models.Message{
		{Role: "user", Content: content, Name: "alice"},
	}

	tokensWithName := EstimateTokens(messages)

	messages2 := []models.Message{
		{Role: "user", Content: content},
	}

	tokensWithout := EstimateTokens(messages2)

	// With name should be slightly more.
	if tokensWithName <= tokensWithout {
		t.Errorf("tokens with name (%d) should be > tokens without (%d)", tokensWithName, tokensWithout)
	}
}

func TestEstimateStringTokens(t *testing.T) {
	tests := []struct {
		input    string
		minToken int
		maxToken int
	}{
		{"", 0, 0},
		{"hello", 1, 3},
		{"Hello, how are you today?", 5, 10},
		{"a", 1, 1},
	}

	for _, tt := range tests {
		tokens := estimateStringTokens(tt.input)
		if tokens < tt.minToken || tokens > tt.maxToken {
			t.Errorf("estimateStringTokens(%q) = %d, want [%d, %d]", tt.input, tokens, tt.minToken, tt.maxToken)
		}
	}
}
