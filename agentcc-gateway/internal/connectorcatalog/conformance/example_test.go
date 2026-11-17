package conformance

import (
	"encoding/json"
	"regexp"
	"testing"
)

func TestExampleArgumentsHoldEveryRequiredProperty(t *testing.T) {
	schema := `{"type":"object","properties":{
		"q":{"type":"string"},
		"n":{"type":"integer","minimum":3,"maximum":50},
		"ratio":{"type":"number","exclusiveMinimum":0.5},
		"flag":{"type":"boolean"},
		"tags":{"type":"array","minItems":2,"items":{"type":"string"}},
		"mode":{"enum":["fast","slow"]},
		"when":{"type":"string","format":"date-time"},
		"id":{"type":"string","pattern":"^n-[0-9]{2,4}$"},
		"nullable":{"type":["null","string"]},
		"nested":{"type":"object","properties":{"inner":{"type":"string"}},"required":["inner"]},
		"optional":{"type":"string"}},
		"required":["q","n","ratio","flag","tags","mode","when","id","nullable","nested"]}`
	var got map[string]any
	if err := json.Unmarshal(exampleArguments(json.RawMessage(schema)), &got); err != nil {
		t.Fatalf("arguments are not a JSON object: %v", err)
	}
	want := map[string]any{
		"q": "cn8", "n": 3.0, "ratio": 1.0, "flag": false, "tags": []any{"cn8", "cn8"}, "mode": "fast",
		"when": "2026-10-19T00:00:00Z", "id": "n-00", "nullable": "cn8", "nested": map[string]any{"inner": "cn8"},
	}
	if g, w := mustJSON(t, got), mustJSON(t, want); g != w {
		t.Fatalf("arguments = %s\nwant        %s", g, w)
	}
	if _, ok := got["optional"]; ok {
		t.Fatalf("an optional property was filled: %v", got)
	}
}

func TestExampleArgumentsWithoutASchemaAreAnEmptyObject(t *testing.T) {
	for _, schema := range []string{``, `null`, `"not a schema"`, `{"type":"object"}`, `{"type":"string"}`} {
		if got := string(exampleArguments(json.RawMessage(schema))); got != `{}` {
			t.Errorf("exampleArguments(%s) = %s, want {}", schema, got)
		}
	}
}

func TestExampleMatchingMatchesThePattern(t *testing.T) {
	for _, pattern := range []string{
		`^[a-z0-9-]+([.][a-z0-9-]+)*[.]snowflakecomputing[.]com$`,
		`^[A-Za-z_][A-Za-z0-9_]{0,254}$`,
		`^[a-z][a-z0-9-]{2,30}$`,
		`^(us|eu)-[0-9]{3}$`,
		`^[A-Za-z0-9-]{8,64}$`,
		`^\d{4}$`,
	} {
		got, err := exampleMatching(pattern)
		if err != nil {
			t.Errorf("exampleMatching(%s): %v", pattern, err)
			continue
		}
		if !regexp.MustCompile(pattern).MatchString(got) {
			t.Errorf("exampleMatching(%s) = %q, which the pattern does not match", pattern, got)
		}
	}
	if got, _ := exampleMatching(`^[a-z][a-z0-9-]{2,30}$`); got != "aaa" {
		t.Errorf("a letter class gives the letter a: got %q, want aaa", got)
	}
}

func TestExampleMatchingRefusesWhatItCannotSatisfy(t *testing.T) {
	for _, pattern := range []string{`^[`, `^\bx\B$`, `^[^\x00-\x{10FFFF}]$`} {
		if got, err := exampleMatching(pattern); err == nil {
			t.Errorf("exampleMatching(%s) = %q, want an error", pattern, got)
		}
	}
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return string(b)
}
