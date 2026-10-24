package validation

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The deny and require patterns read the arguments of the request's tool
// calls; the length rules count each message's content: an assistant turn
// that only calls a tool has no content, and its arguments are not a message
// of their own that min_message_length could find too short, nor do they
// count toward max_message_length or max_total_length.
func toolCallRequest(arguments string) *guardrails.CheckInput {
	return &guardrails.CheckInput{Request: &models.ChatCompletionRequest{Messages: []models.Message{
		{Role: "user", Content: []byte(`"Look the record up, please."`)},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_1", Type: "function",
			Function: models.FunctionCall{Name: "lookup", Arguments: arguments}}}},
		{Role: "tool", ToolCallID: "call_1", Content: []byte(`"Found the record you asked for."`)},
	}}}
}

func TestValidation_LengthRulesCountContentNotArguments(t *testing.T) {
	long := `{"query":"a very long query that runs well past forty characters in all"}`
	for _, tc := range []struct {
		name string
		cfg  map[string]interface{}
		args string
	}{
		{"min_message_length", map[string]interface{}{"min_message_length": 20}, `{"id":"r-7"}`},
		{"max_message_length", map[string]interface{}{"max_message_length": 40}, long},
		{"max_total_length", map[string]interface{}{"max_total_length": 70}, long},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := New(tc.cfg).Check(context.Background(), toolCallRequest(tc.args))
			if r == nil || !r.Pass {
				t.Errorf("%s refused a request whose messages' content passes it: %+v", tc.name, r)
			}
		})
	}
}

// A require pattern is met by text in a tool call's arguments, as a deny
// pattern is found there.
func TestValidation_RequirePatternReadsArguments(t *testing.T) {
	g := New(map[string]interface{}{"require_patterns": []interface{}{"ticket-[0-9]+"}})
	if r := g.Check(context.Background(), toolCallRequest(`{"ticket":"ticket-42"}`)); r == nil || !r.Pass {
		t.Errorf("a require pattern met only in a tool call's arguments failed: %+v", r)
	}
	if r := g.Check(context.Background(), toolCallRequest(`{"ticket":"none"}`)); r == nil || r.Pass {
		t.Errorf("a require pattern met nowhere passed: %+v", r)
	}
}
