package openai

import "testing"

// consoleRefusalCodes are the codes the Console's chat reads as the gateway's
// own refusal. A provider answering with one reaches the client as
// provider_error, with its code kept in metadata.provider_code.
var consoleRefusalCodes = []string{
	"managed_credits_exhausted",
	"managed_credits_insufficient",
	"managed_billing_unavailable",
	"future_agi_credits_off",
	"managed_models_disabled",
	"managed_spend_paused",
	"org_budget_exceeded",
	"agent_call_cap_exceeded",
	"agent_budget_exhausted",
	"agent_key_budget_exhausted",
	// Gateway-only refusal names outside the reserved families, reserved by
	// name: the Console reads them as the gateway's own refusal too.
	"call_cap_unenforceable",
	"run_budget_unenforceable",
	"budget_state_unavailable",
	"budget_throttled",
	"usage_paused",
}

func TestAProviderCannotAnswerWithAConsoleRefusalCode(t *testing.T) {
	for _, code := range consoleRefusalCodes {
		e := parseProviderError(402, reservedCodeBody(code))
		if e.Code != "provider_error" {
			t.Errorf("%s: code %q, want provider_error", code, e.Code)
		}
		if got := keptProviderCode(e); got != code {
			t.Errorf("%s: provider_code %v, want %q", code, got, code)
		}
	}
}
