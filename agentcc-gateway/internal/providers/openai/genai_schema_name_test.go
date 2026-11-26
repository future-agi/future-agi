package openai

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// OpenAI and the OpenAI-compatible presets get response_format as sent:
// json_schema with the "response" name a GenAI request is given, which OpenAI
// requires (and Mistral too) and the others accept as part of OpenAI's shape.
func TestGenAISchemaNameIsSentToOpenAICompatibleProviders(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:          "gpt-4o-mini",
		Messages:       []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		ResponseFormat: &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"response","schema":{"type":"object"}}`)},
	}
	body, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), `"json_schema":{"name":"response","schema":{"type":"object"}}`) {
		t.Fatalf("response_format not carried with its name: %s", body)
	}
}
