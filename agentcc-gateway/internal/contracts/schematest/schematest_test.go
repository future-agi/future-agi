package schematest

import (
	"encoding/json"
	"testing"
)

func parse(t *testing.T, s string) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal([]byte(s), &v); err != nil {
		t.Fatal(err)
	}
	return v
}

// The contract goldens pass their own schemas, including the anyOf in
// capabilities-v1, and Validate takes a Go value as well as a decoded one.
func TestGoldensPassTheirSchemas(t *testing.T) {
	for _, name := range []string{"log-entry-v2", "outcome-event-v1", "capabilities-v1"} {
		golden := ReadExample(t, name+".json")
		if errs := Validate(t, name+".schema.json", golden); len(errs) > 0 {
			t.Errorf("%s fails its schema: %v", name, errs)
		}
	}
	type outcome struct {
		Kind string `json:"kind"`
	}
	if errs := Validate(t, "outcome-event-v1.schema.json", outcome{Kind: "outcome"}); len(errs) == 0 {
		t.Error("a struct missing the required fields passed")
	}
}

// ValidateDef checks against one definition: a golden attempt passes, and one
// without its outcome does not.
func TestValidateDef(t *testing.T) {
	attempt := ReadExample(t, "log-entry-v2.json")["attempts"].([]any)[0].(map[string]any)
	if errs := ValidateDef(t, "log-entry-v2.schema.json", "Attempt", attempt); len(errs) > 0 {
		t.Fatalf("the golden attempt fails $defs/Attempt: %v", errs)
	}
	delete(attempt, "outcome")
	if errs := ValidateDef(t, "log-entry-v2.schema.json", "Attempt", attempt); len(errs) == 0 {
		t.Error("an attempt without its outcome passed")
	}
}

// The checker fails on what it does not check, rather than passing it.
func TestUnsupportedSchemaFails(t *testing.T) {
	for name, schema := range map[string]string{
		"unknown keyword":             `{"type":"object","patternProperties":{"^x":{"type":"string"}}}`,
		"type list":                   `{"type":["string","null"]}`,
		"format other than date-time": `{"type":"string","format":"email"}`,
		"unresolvable $ref":           `{"$ref":"#/$defs/Missing"}`,
	} {
		root := parse(t, schema)
		if errs := validate(root, root, "x"); len(errs) == 0 {
			t.Errorf("%s: the checker passed it", name)
		}
	}
}

// Each keyword rejects what it forbids.
func TestKeywordsReject(t *testing.T) {
	root := parse(t, `{
		"$defs": {"Id": {"type": "string", "pattern": "^id_", "minLength": 4, "maxLength": 8}},
		"type": "object",
		"required": ["id", "n"],
		"additionalProperties": false,
		"properties": {
			"id": {"$ref": "#/$defs/Id"},
			"n": {"type": "integer", "minimum": 1, "maximum": 3},
			"kind": {"const": "a"},
			"mode": {"enum": ["x", "y"]},
			"at": {"type": "string", "format": "date-time"},
			"tags": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string"}},
			"either": {"anyOf": [{"type": "string"}, {"type": "null"}]}
		}
	}`)
	ok := `{"id":"id_1","n":2,"kind":"a","mode":"x","at":"2026-10-15T09:30:12Z","tags":["t"],"either":null}`
	if errs := validate(root, root, parse(t, ok)); len(errs) > 0 {
		t.Fatalf("a conforming value failed: %v", errs)
	}
	for name, mutate := range map[string]func(m map[string]any){
		"missing required": func(m map[string]any) { delete(m, "n") },
		"unknown key":      func(m map[string]any) { m["surprise"] = 1.0 },
		"$ref pattern":     func(m map[string]any) { m["id"] = "xx_1" },
		"min length":       func(m map[string]any) { m["id"] = "id_" },
		"max length":       func(m map[string]any) { m["id"] = "id_123456" },
		"fractional int":   func(m map[string]any) { m["n"] = 1.5 },
		"minimum":          func(m map[string]any) { m["n"] = 0.0 },
		"maximum":          func(m map[string]any) { m["n"] = 4.0 },
		"const":            func(m map[string]any) { m["kind"] = "b" },
		"enum":             func(m map[string]any) { m["mode"] = "z" },
		"date-time":        func(m map[string]any) { m["at"] = "yesterday" },
		"min items":        func(m map[string]any) { m["tags"] = []any{} },
		"max items":        func(m map[string]any) { m["tags"] = []any{"a", "b", "c"} },
		"item type":        func(m map[string]any) { m["tags"] = []any{1.0} },
		"no anyOf branch":  func(m map[string]any) { m["either"] = 1.0 },
	} {
		m := parse(t, ok)
		mutate(m)
		if errs := validate(root, root, m); len(errs) == 0 {
			t.Errorf("%s: %v passed", name, m)
		}
	}
}
