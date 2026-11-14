package cohere

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Cohere has no json_schema: a GenAI responseSchema (named "response") falls
// back to json_object, so the name is never sent.
func TestGenAISchemaNameIsNotSentToCohere(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:          "command-r",
		Messages:       []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		ResponseFormat: &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"response","schema":{"type":"object"}}`)},
	}
	out := translateRequest(req)
	body, _ := json.Marshal(out)
	if out.ResponseFormat == nil || out.ResponseFormat.Type != "json_object" {
		t.Fatalf("response_format = %+v, want json_object", out.ResponseFormat)
	}
	if strings.Contains(string(body), `"response"`) {
		t.Errorf("the schema name was sent: %s", body)
	}
}

