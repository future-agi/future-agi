package guardrails_test

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
)

// "judges" is "says", "shape", or unset (absent or null); anything else is
// an error, which the check reports as its config error.
func TestParseJudges(t *testing.T) {
	for _, tc := range []struct {
		name  string
		cfg   map[string]interface{}
		want  string
		error bool
	}{
		{"nil config", nil, "", false},
		{"absent", map[string]interface{}{"url": "x"}, "", false},
		{"null", map[string]interface{}{"judges": nil}, "", false},
		{"says", map[string]interface{}{"judges": "says"}, guardrails.JudgesSays, false},
		{"shape", map[string]interface{}{"judges": "shape"}, guardrails.JudgesShape, false},
		{"empty", map[string]interface{}{"judges": ""}, "", true},
		{"other word", map[string]interface{}{"judges": "content"}, "", true},
		{"case", map[string]interface{}{"judges": "Shape"}, "", true},
		{"bool", map[string]interface{}{"judges": true}, "", true},
		{"list", map[string]interface{}{"judges": []interface{}{"shape"}}, "", true},
	} {
		got, err := guardrails.ParseJudges(tc.cfg)
		if got != tc.want || (err != nil) != tc.error {
			t.Errorf("%s: ParseJudges = %q, %v; want %q, error %v", tc.name, got, err, tc.want, tc.error)
		}
	}
}

// A built-in check's kind is fixed by its type.
func TestCheckKindOfBuiltInChecks(t *testing.T) {
	for _, tc := range []struct {
		g    guardrails.Guardrail
		kind string
	}{
		{validation.New(nil), "shape"},
		{blocklist.New(nil), "says"},
	} {
		if kind, source := guardrails.CheckKind(tc.g); kind != tc.kind || source != "built-in" {
			t.Errorf("%s: CheckKind = %s, %s; want %s, built-in", tc.g.Name(), kind, source, tc.kind)
		}
	}
}
