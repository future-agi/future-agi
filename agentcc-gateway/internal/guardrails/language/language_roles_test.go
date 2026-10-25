package language

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// language-detection judges the language of what the user wrote: it reads
// the request's user messages.
func TestReadsUserMessagesOnly(t *testing.T) {
	cyrillic, _ := json.Marshal("Привет как дела сегодня друзья")
	latin, _ := json.Marshal("Hello, how are you today?")
	in := &guardrails.CheckInput{Request: &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "system", Content: cyrillic},
		{Role: "user", Content: latin},
		{Role: "assistant", Content: cyrillic, ToolCalls: []models.ToolCall{{ID: "c", Type: "function",
			Function: models.FunctionCall{Name: "f", Arguments: `{"q":"Привет как дела сегодня друзья"}`}}}},
		{Role: "tool", ToolCallID: "c", Content: cyrillic},
	}}, Metadata: map[string]string{}}
	g := New(map[string]interface{}{"allowed_scripts": []interface{}{"latin"}})
	if r := g.Check(context.Background(), in); !r.Pass {
		t.Errorf("text outside the user's messages was read: %+v", r)
	}
}
