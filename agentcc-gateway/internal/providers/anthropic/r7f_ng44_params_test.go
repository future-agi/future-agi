package anthropic

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The rest of the release notes' Known-limits list for this adapter: a
// response_format of type json_object and a request for extended thinking
// (thinking, thinking_budget or reasoning_effort) are not sent; a
// json_schema response_format is.
func TestR7fJSONObjectAndThinkingThisAdapterDoesNotSend(t *testing.T) {
	req := r7fRequest(&models.ResponseFormat{Type: "json_object"})
	out := r7iTranslated(t, req)
	for _, field := range []string{"json_object", "output_config", "thinking", "budget_tokens", "1777", "reasoning", "effort"} {
		if strings.Contains(strings.ToLower(out), field) {
			t.Errorf("translated request %s carries %s", out, field)
		}
	}

	// json_schema is sent, so the check above can see a sent format.
	schema := r7iTranslated(t, r7fRequest(&models.ResponseFormat{
		Type:       "json_schema",
		JSONSchema: json.RawMessage(`{"name":"r7f","schema":{"type":"object","properties":{"r7fmarker":{"type":"string"}}}}`),
	}))
	if !strings.Contains(schema, `"output_config":{"format":{"type":"json_schema"`) || !strings.Contains(schema, "r7fmarker") {
		t.Errorf("a json_schema response_format is not sent: %s", schema)
	}
}

func r7fRequest(rf *models.ResponseFormat) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Model:          "claude-sonnet-4-5",
		Messages:       []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		ResponseFormat: rf,
		Extra: map[string]json.RawMessage{
			"thinking":         json.RawMessage(`{"type":"enabled","budget_tokens":1777}`),
			"thinking_budget":  json.RawMessage(`1777`),
			"reasoning_effort": json.RawMessage(`"high"`),
		},
	}
}
