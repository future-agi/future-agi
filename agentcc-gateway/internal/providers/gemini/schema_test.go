package gemini

import (
	"encoding/json"
	"testing"
)

func TestNormalizeToolSchemaForVertexFunctionDeclarations(t *testing.T) {
	raw := json.RawMessage(`{
		"type":"object",
		"properties":{
			"name":{"type":["string","null"],"enum":["a",null]},
			"values":{"type":"array"}
		}
	}`)

	got := normalizeToolSchema(raw)
	var schema map[string]any
	if err := json.Unmarshal(got, &schema); err != nil {
		t.Fatal(err)
	}
	properties := schema["properties"].(map[string]any)
	name := properties["name"].(map[string]any)
	if name["type"] != "string" {
		t.Fatalf("type = %#v, want string", name["type"])
	}
	if len(name["enum"].([]any)) != 1 {
		t.Fatalf("enum = %#v, want null removed", name["enum"])
	}
	values := properties["values"].(map[string]any)
	if _, ok := values["items"].(map[string]any); !ok {
		t.Fatalf("items = %#v, want an explicit schema", values["items"])
	}
}
