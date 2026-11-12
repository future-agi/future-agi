package bedrock

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The rest of the release notes' Known-limits list for this adapter, in both
// the invoke and the Converse request: a response_format of type json_object
// and a request for extended thinking (thinking, thinking_budget or
// reasoning_effort) are not sent; a json_schema response_format is.
func TestR7fJSONObjectAndThinkingThisAdapterDoesNotSend(t *testing.T) {
	out := r7iTranslated(t, r7fRequest(&models.ResponseFormat{Type: "json_object"}))
	for _, field := range []string{"json_object", "json_schema", "thinking", "budget_tokens", "1777", "reasoning", "effort", "additionalmodelrequestfields"} {
		if strings.Contains(strings.ToLower(out), field) {
			t.Errorf("translated requests %s carry %s", out, field)
		}
	}

	// json_schema is sent in both forms, so the check above can see a sent
	// format.
	req := r7fRequest(&models.ResponseFormat{
		Type:       "json_schema",
		JSONSchema: json.RawMessage(`{"name":"r7f","schema":{"type":"object","properties":{"r7fmarker":{"type":"string"}}}}`),
	})
	br, _ := translateRequest(req)
	cr, _ := buildConverseRequest(req)
	for name, v := range map[string]any{"invoke": br, "converse": cr} {
		b, _ := json.Marshal(v)
		if !strings.Contains(string(b), "json_schema") || !strings.Contains(string(b), "r7fmarker") {
			t.Errorf("%s: a json_schema response_format is not sent: %s", name, b)
		}
	}
}

func r7fRequest(rf *models.ResponseFormat) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Model:          "anthropic.claude-3-5-sonnet-20240620-v1:0",
		Messages:       []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		ResponseFormat: rf,
		Extra: map[string]json.RawMessage{
			"thinking":         json.RawMessage(`{"type":"enabled","budget_tokens":1777}`),
			"thinking_budget":  json.RawMessage(`1777`),
			"reasoning_effort": json.RawMessage(`"high"`),
		},
	}
}
