package anthropic

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A GenAI responseSchema reaches every provider as json_schema named
// "response" (translation/gemini). Anthropic's output_config takes only the
// schema, so the name changes nothing.
func TestGenAISchemaNameIsNotSentToAnthropic(t *testing.T) {
	schema := `{"type":"object","properties":{"city":{"type":"string"}}}`
	rf := &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"response","schema":` + schema + `}`)}
	if got := string(extractResponseFormatSchema(rf)); got != schema {
		t.Fatalf("schema = %s, want %s", got, schema)
	}
}
