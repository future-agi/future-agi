package models

import "testing"

// consoleRefusalCodes are the codes the Console's chat reads as the gateway's
// own refusal (out of credits, spend paused, out of budget, over a cap). Each
// must stay reserved, so a provider's answer can never carry one to a client.
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

func TestEveryConsoleRefusalCodeIsReserved(t *testing.T) {
	for _, code := range consoleRefusalCodes {
		if !IsGatewayCode(code) {
			t.Errorf("%s is not reserved: a provider's answer could carry it to the Console", code)
		}
	}
}
