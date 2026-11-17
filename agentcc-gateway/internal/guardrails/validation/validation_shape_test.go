package validation_test

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
)

// input-validation judges the answer's shape (patterns the whole text must
// match, lengths), so it does not apply to a model's refusal. Checks that
// judge what the text says read a refusal as content; expression is one of
// them, as the admin's expression decides what it judges.
func TestWhichChecksJudgeShape(t *testing.T) {
	for _, tc := range []struct {
		g     guardrails.Guardrail
		shape bool
	}{
		{validation.New(nil), true},
		{blocklist.New(nil), false},
		{pii.New(nil), false},
		{expression.New("expr", map[string]interface{}{"expression": `request.model == "gpt-4o"`}), false},
	} {
		if got := guardrails.IsShapeCheck(tc.g); got != tc.shape {
			t.Errorf("%s: IsShapeCheck = %v, want %v", tc.g.Name(), got, tc.shape)
		}
	}
}
