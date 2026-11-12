package guardrails_test

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
)

// How each kind of check reads a model's refusal: a built-in check that
// judges what the text says reads it; input-validation, which judges the
// answer's shape, does not; a check whose kind the gateway cannot tell (a
// Future AGI evaluation, a webhook, an external evaluator) does not, as in
// production. expression is a says check: the admin's expression decides.
func TestCheckKindsForARefusal(t *testing.T) {
	for _, tc := range []struct {
		name    string
		g       guardrails.Guardrail
		shape   bool
		unknown bool
	}{
		{"input-validation", validation.New(nil), true, false},
		{"keyword-blocklist", blocklist.New(nil), false, false},
		{"pii-detection", pii.New(nil), false, false},
		{"data-leakage-prevention", leakage.New(nil), false, false},
		{"expression", expression.New("expr", map[string]interface{}{"expression": `request.model == "gpt-4o"`}), false, false},
		{"futureagi", &futureagi.FutureAGIGuardrail{}, false, true},
		{"webhook", &webhook.WebhookGuardrail{}, false, true},
		{"external", &external.ExternalGuardrail{}, false, true},
	} {
		if got := guardrails.IsShapeCheck(tc.g); got != tc.shape {
			t.Errorf("%s: IsShapeCheck = %v, want %v", tc.name, got, tc.shape)
		}
		if got := guardrails.IsUnknownKindCheck(tc.g); got != tc.unknown {
			t.Errorf("%s: IsUnknownKindCheck = %v, want %v", tc.name, got, tc.unknown)
		}
	}
}
