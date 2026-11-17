package server

// The answer a client gets for a provider's failure never carries one of the
// gateway's own refusal codes: whatever adapter built the error, a provider
// code that is the gateway's is answered as provider_error, and the
// provider's code is kept in metadata.provider_code. A provider's ordinary
// code is kept as it is.

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

var reservedGatewayCodes = []string{
	"managed_credits_exhausted",
	"agent_budget_exhausted",
	"managed_upstream_unavailable",
	"agent_run_stopped",
	"future_agi_account_suspended",
	"org_budget_exceeded",
}

func reservedCodeAnswer(code string, metadata any) *models.APIError {
	return &models.APIError{Status: 400, Type: "invalid_request_error", Code: code,
		Message: "upstream said no", ProviderStatus: 400, UpstreamStatus: 400, Metadata: metadata}
}

func TestTheClientNeverGetsTheGatewaysCodeFromAProvider(t *testing.T) {
	for _, code := range reservedGatewayCodes {
		// An adapter that copied the code as it is.
		got := attributeProviderFailure(reservedCodeAnswer(code, nil), "my-openai", credentialOrg)
		meta, _ := got.Metadata.(map[string]any)
		if got.Code != "provider_error" || meta["provider_code"] != code || meta["provider"] != "my-openai" {
			t.Errorf("%s copied: code %q, metadata %v", code, got.Code, got.Metadata)
		}
		// An adapter that already answered provider_error and kept the code.
		kept := reservedCodeAnswer("provider_error", map[string]any{"provider_code": code})
		got = attributeProviderFailure(kept, "my-openai", credentialOrg)
		meta, _ = got.Metadata.(map[string]any)
		if got.Code != "provider_error" || meta["provider_code"] != code {
			t.Errorf("%s kept: code %q, metadata %v", code, got.Code, got.Metadata)
		}
	}
}

func TestAProvidersOrdinaryCodeIsKept(t *testing.T) {
	for _, code := range []string{"rate_limit_exceeded", "invalid_api_key", "model_not_found"} {
		got := attributeProviderFailure(reservedCodeAnswer(code, nil), "openai", credentialGateway)
		meta, _ := got.Metadata.(map[string]any)
		if got.Code != code || meta["provider_code"] != code {
			t.Errorf("%s: code %q, metadata %v", code, got.Code, got.Metadata)
		}
	}
}
