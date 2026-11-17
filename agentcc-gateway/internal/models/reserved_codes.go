package models

import "strings"

// CodeProviderError is the code a provider's error answers with when the
// provider's own code is one of the gateway's refusal codes (IsGatewayCode).
// The provider's code is kept beside it, in metadata.provider_code.
const CodeProviderError = "provider_error"

// gatewayCodes are refusal codes only the gateway answers with: a client
// reads them as the gateway's own refusal (out of credits, out of budget),
// never as a provider's.
var gatewayCodes = map[string]struct{}{
	"managed_credits_exhausted": {},
	"agent_budget_exhausted":    {},
	// Gateway-only refusal names outside the families below.
	"call_cap_unenforceable":   {},
	"run_budget_unenforceable": {},
	"budget_state_unavailable": {},
	"budget_throttled":         {},
	"usage_paused":             {},
}

// gatewayCodePrefixes name the gateway's families of refusal codes.
var gatewayCodePrefixes = []string{"managed_", "agent_", "future_agi_", "org_budget_"}

// IsGatewayCode reports whether code is one the gateway keeps for its own
// refusals, so a provider's error may not carry it to a client.
func IsGatewayCode(code string) bool {
	if _, ok := gatewayCodes[code]; ok {
		return true
	}
	for _, prefix := range gatewayCodePrefixes {
		if strings.HasPrefix(code, prefix) {
			return true
		}
	}
	return false
}

// ReserveGatewayCode makes e, an error an adapter parsed from a provider's
// answer, answer with CodeProviderError when the provider's code is one of
// the gateway's, keeping the provider's code in metadata.provider_code. Any
// other code is left as it is.
func ReserveGatewayCode(e *APIError) {
	if e == nil || !IsGatewayCode(e.Code) {
		return
	}
	meta, ok := e.Metadata.(map[string]any)
	if !ok || meta == nil {
		meta = map[string]any{}
		if e.Metadata != nil {
			meta["detail"] = e.Metadata
		}
	}
	meta["provider_code"] = e.Code
	e.Metadata = meta
	e.Code = CodeProviderError
}
