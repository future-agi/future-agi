package restmcp

import (
	"encoding/json"
	"strings"
	"testing"
)

func scalarValidationTool(t *testing.T, property string, required bool) *Tool {
	t.Helper()
	extra := ""
	if required {
		extra = `,"required":["body"]`
	}
	s, err := parseInputSchema(json.RawMessage(`{"type":"object","properties":{"body":` + property + `}` + extra + `}`))
	if err != nil {
		t.Fatal(err)
	}
	return &Tool{schema: s, method: "POST", path: "/scalar"}
}

func TestScalarValidation(t *testing.T) {
	const integer = `{"type":"integer","minimum":1,"maximum":1000}`
	const boolean = `{"type":"boolean"}`
	for _, tc := range []struct {
		name, property, value string
		reject                bool
	}{
		{"lower", integer, `1`, false},
		{"upper", integer, `1000`, false},
		{"decimal integer", integer, `1.0`, false},
		{"exponent integer", integer, `1e0`, false},
		{"exponent upper", integer, `1e3`, false},
		{"under", integer, `0`, true},
		{"over", integer, `1001`, true},
		{"fraction", integer, `1.5`, true},
		{"quoted", integer, `"1"`, true},
		{"bool as integer", integer, `true`, true},
		{"small lower", `{"type":"integer","minimum":1,"maximum":100}`, `1`, false},
		{"small upper", `{"type":"integer","minimum":1,"maximum":100}`, `100`, false},
		{"small under", `{"type":"integer","minimum":1,"maximum":100}`, `0`, true},
		{"small over", `{"type":"integer","minimum":1,"maximum":100}`, `101`, true},
		{"true", boolean, `true`, false},
		{"false", boolean, `false`, false},
		{"quoted boolean", boolean, `"true"`, true},
		{"numeric boolean", boolean, `1`, true},
		{"array boolean", boolean, `[]`, true},
		{"object boolean", boolean, `{}`, true},
		{"zero", `{"type":"number","minimum":0,"maximum":0}`, `0`, false},
		{"negative zero", `{"type":"number","minimum":0,"maximum":0}`, `-0`, false},
		{"under zero", `{"type":"number","minimum":0,"maximum":0}`, `-1`, true},
		{"over zero", `{"type":"number","minimum":0,"maximum":0}`, `1`, true},
		{"exact decimal", `{"type":"number","minimum":0.1,"maximum":0.1}`, `1e-1`, false},
		{"decimal neighbor", `{"type":"number","maximum":0.1}`, `0.10000000000000001`, true},
		{"string union", `{"type":["integer","string"],"minimum":2}`, `"1"`, false},
		{"union fraction", `{"type":["integer","string"]}`, `1.5`, true},
		{"numeric union", `{"type":["integer","number"]}`, `1.5`, false},
		{"union bounds", `{"type":["integer","string"],"minimum":2}`, `1`, true},
		{"boolean union", `{"type":["boolean","string"]}`, `"true"`, false},
		{"boolean union number", `{"type":["boolean","string"]}`, `1`, true},
		{"mixed array", `{"type":["integer","array"],"minimum":2}`, `[1.5]`, false},
		{"mixed array string", `{"type":["integer","array"]}`, `"1"`, true},
		{"mixed array fraction", `{"type":["integer","array"]}`, `1.5`, true},
		{"mixed object", `{"type":["boolean","object"]}`, `{"x":1}`, false},
		{"mixed object number", `{"type":["boolean","object"]}`, `1`, true},
		{"bounds only string", `{"minimum":2}`, `"1"`, false},
		{"bounds only bool", `{"minimum":2}`, `true`, false},
		{"bounds only array", `{"minimum":2}`, `[1]`, false},
		{"bounds only number", `{"minimum":2}`, `1`, true},
		{"precise endpoint", `{"type":"integer","maximum":9007199254740992}`, `9007199254740992`, false},
		{"precise neighbor", `{"type":"integer","maximum":9007199254740992}`, `9007199254740993`, true},
		{"precise fraction", `{"type":"integer"}`, `9007199254740992.5`, true},
		{"tiny integer", `{"type":"integer"}`, `1e-1000`, true},
		{"tiny number", `{"type":"number","minimum":1e-1000}`, `1e-1000`, false},
		{"tiny below", `{"type":"number","minimum":1e-1000}`, `1e-1001`, true},
		{"positive exponent limit", `{"type":"number"}`, `1e4096`, false},
		{"negative exponent limit", `{"type":"number"}`, `1e-4096`, false},
		{"positive exponent over", `{"type":"number"}`, `1e4097`, true},
		{"negative exponent over", `{"type":"number"}`, `1e-4097`, true},
		{"huge positive exponent", `{"type":"number"}`, `1e999999999`, true},
		{"huge negative exponent", `{"type":"number"}`, `1e-999999999`, true},
		{"overflow exponent", `{"type":"number"}`, `1e999999999999999999999`, true},
		{"lexeme limit", `{"type":"integer"}`, "1" + strings.Repeat("0", 1023), false},
		{"lexeme over", `{"type":"integer"}`, "1" + strings.Repeat("0", 1024), true},
		// Unknown types retain the existing placement-only behavior.
		{"unknown type", `{"type":"custom"}`, `1`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tool := scalarValidationTool(t, tc.property, false)
			req, err := tool.buildRequest(map[string]json.RawMessage{"body": json.RawMessage(tc.value)})
			if (err != nil) != tc.reject {
				t.Fatalf("error=%v, reject=%v", err, tc.reject)
			}
			if err != nil {
				if _, ok := err.(*argError); !ok {
					t.Fatalf("not a caller error: %T", err)
				}
			} else if string(req.body) != tc.value {
				t.Fatalf("body=%s, want %s", req.body, tc.value)
			}
		})
	}
}

func TestScalarValidationPresenceAndConst(t *testing.T) {
	for _, tc := range []struct {
		name, property, value, body string
		required, reject            bool
	}{
		{"missing optional", `{"type":"integer"}`, "", "", false, false},
		{"null optional", `{"type":["integer","null"]}`, "null", "", false, false},
		{"null nonnullable", `{"type":"integer"}`, "null", "", false, false},
		{"missing required", `{"type":"integer"}`, "", "", true, true},
		{"null required", `{"type":["integer","null"]}`, "null", "", true, true},
		{"const missing", `{"type":"integer","const":2}`, "", "2", true, false},
		{"const null caller", `{"type":"integer","const":2}`, "null", "2", true, false},
		{"const same", `{"type":"integer","const":2}`, "2", "2", true, false},
		{"const mismatch", `{"type":"integer","const":2}`, "3", "", false, true},
		{"const bound", `{"type":"integer","const":2,"maximum":1}`, "", "", false, true},
		{"const null", `{"type":["integer","null"],"const":null}`, "", "null", true, false},
		{"legacy const null", `{"type":"integer","const":null}`, "", "null", true, false},
		{"const object", `{"type":"object","const":{"x-mode":1}}`, "", `{"x-mode":1}`, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tool := scalarValidationTool(t, tc.property, tc.required)
			args := map[string]json.RawMessage{}
			if tc.value != "" {
				args["body"] = json.RawMessage(tc.value)
			}
			req, err := tool.buildRequest(args)
			if (err != nil) != tc.reject {
				t.Fatalf("error=%v, reject=%v", err, tc.reject)
			}
			if err == nil && string(req.body) != tc.body {
				t.Fatalf("body=%s, want %s", req.body, tc.body)
			}
		})
	}
}

func TestScalarValidationMalformedBounds(t *testing.T) {
	for _, keyword := range []string{"minimum", "maximum"} {
		for _, value := range []string{`"1"`, `null`, `true`, `{}`, `[]`, `"NaN"`, `1e999999999`, `1e-999999999`, "1" + strings.Repeat("0", 1024)} {
			_, err := parseInputSchema(json.RawMessage(`{"properties":{"value":{"` + keyword + `":` + value + `}}}`))
			if err == nil || !strings.Contains(err.Error(), keyword) || !strings.Contains(err.Error(), "value") {
				t.Fatalf("%s=%s: error=%v", keyword, value, err)
			}
		}
	}
}

func TestScalarValidationMalformedNumbers(t *testing.T) {
	tool := scalarValidationTool(t, `{"type":"number"}`, false)
	for _, raw := range []string{"", "NaN", "Infinity", "+1", "01", "1/2", "0x10", "1e", "1 2", "1\ntrue"} {
		if _, err := tool.buildRequest(map[string]json.RawMessage{"body": json.RawMessage(raw)}); err == nil {
			t.Errorf("accepted malformed number %q", raw)
		}
	}
}

func TestScalarValidationLegacyPlacement(t *testing.T) {
	s, err := parseInputSchema(json.RawMessage(`{"properties":{"id":{"type":"string"},"tags":{"type":"array","items":{"type":"string"}}},"required":["id"]}`))
	if err != nil {
		t.Fatal(err)
	}
	tool := &Tool{schema: s, method: "GET", path: "/items/{id}", pathParams: []string{"id"}}
	req, err := tool.buildRequest(map[string]json.RawMessage{"id": json.RawMessage(`42`), "tags": json.RawMessage(`"single"`)})
	if err != nil {
		t.Fatal(err)
	}
	if req.path != "/items/42" || encodeQuery(req.query) != "tags=single" {
		t.Fatalf("path=%s query=%s", req.path, encodeQuery(req.query))
	}
}
