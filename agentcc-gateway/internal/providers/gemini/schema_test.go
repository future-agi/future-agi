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

func TestNormalizeToolSchemaPreservesPropertyNamesMatchingKeywords(t *testing.T) {
	raw := json.RawMessage(`{
		"type":"object",
		"properties":{
			"default":{"type":"string","default":"sample"},
			"additionalProperties":{"type":"array"},
			"type":{"type":"object","properties":{"enum":{"type":["string","null"]}}}
		},
		"required":["default","additionalProperties","type"]
	}`)

	got := normalizeToolSchema(raw)
	var schema map[string]any
	if err := json.Unmarshal(got, &schema); err != nil {
		t.Fatal(err)
	}
	properties := schema["properties"].(map[string]any)
	for _, name := range []string{"default", "additionalProperties", "type"} {
		if _, ok := properties[name]; !ok {
			t.Fatalf("property %q was removed: %s", name, got)
		}
	}
	if _, ok := properties["default"].(map[string]any)["default"]; ok {
		t.Fatalf("unsupported default keyword was retained inside property schema: %s", got)
	}
	if _, ok := properties["additionalProperties"].(map[string]any)["items"]; !ok {
		t.Fatalf("array property schema was not normalized: %s", got)
	}
	nested := properties["type"].(map[string]any)["properties"].(map[string]any)["enum"].(map[string]any)
	if nested["type"] != "string" {
		t.Fatalf("nested schema type = %#v, want string", nested["type"])
	}
	if gotRequired := schema["required"].([]any); len(gotRequired) != 3 {
		t.Fatalf("required = %#v, want all property names", gotRequired)
	}
}
