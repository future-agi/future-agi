package gemini

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A GenAI responseSchema that went through the chat shape comes back to Gemini
// as responseSchema alone: the "response" name is not sent.
func TestGenAISchemaNameIsNotSentToGemini(t *testing.T) {
	schema := `{"type":"object","properties":{"city":{"type":"string"}}}`
	req := &models.ChatCompletionRequest{
		Model:          "gemini-2.5-flash",
		Messages:       []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		ResponseFormat: &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"response","schema":` + schema + `}`)},
	}
	out, _ := translateRequest(req)
	if out.GenerationConfig == nil || string(out.GenerationConfig.ResponseSchema) != schema {
		t.Fatalf("responseSchema = %+v, want %s", out.GenerationConfig, schema)
	}
}
