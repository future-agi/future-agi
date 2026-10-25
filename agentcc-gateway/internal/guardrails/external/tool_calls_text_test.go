package external

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The text this check sends for a request holds the arguments of the
// request's tool calls and the tools' results, as it holds every message's
// text.
func TestInputText_HoldsToolCallArgumentsAndResults(t *testing.T) {
	in := &guardrails.CheckInput{Request: &models.ChatCompletionRequest{Messages: []models.Message{
		{Role: "user", Content: []byte(`"Look the record up."`)},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_1", Type: "function",
			Function: models.FunctionCall{Name: "lookup", Arguments: `{"ssn":"123\u002d45\u002d6789"}`}}}},
		{Role: "tool", ToolCallID: "call_1", Content: []byte(`[{"type":"text","text":"Card 4111 1111 1111 1111"}]`)},
	}}}
	text := extractInputText(context.Background(), in)
	for _, want := range []string{"Look the record up.", "ssn: 123-45-6789", "Card 4111 1111 1111 1111"} {
		if !strings.Contains(text, want) {
			t.Errorf("the text sent is %q, which lacks %q", text, want)
		}
	}
}
