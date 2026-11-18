package bedrock

import (
	"encoding/json"
	"regexp"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A GenAI responseSchema reaches every provider as json_schema named
// "response" (translation/gemini). Converse's outputConfig jsonSchema carries a
// name, and "response" is a valid one; InvokeModel takes only the schema.
func TestGenAISchemaNameOnBedrock(t *testing.T) {
	schema := `{"type":"object","properties":{"city":{"type":"string"}}}`
	rf := &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"response","schema":` + schema + `}`)}

	format := buildConverseOutputFormat(rf)
	if format == nil {
		t.Fatal("no Converse output format")
	}
	if name := format.Structure.JSONSchema.Name; !regexp.MustCompile(`^[a-zA-Z0-9_-]{1,64}$`).MatchString(name) || name != "response" {
		t.Errorf("Converse jsonSchema name = %q, want response", name)
	}
	if got := string(extractResponseFormatSchema(rf)); got != schema {
		t.Errorf("InvokeModel schema = %s, want %s", got, schema)
	}
}
