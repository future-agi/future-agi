package openai

// A provider's error code that is one of the gateway's own refusal codes
// reaches the client as provider_error, with the provider's code kept in
// metadata.provider_code: a client never reads an upstream's answer as the
// gateway's own refusal. Every other provider code passes through unchanged.

import (
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// reservedGatewayCodes is one code per reserved name and per reserved prefix.
var reservedGatewayCodes = []string{
	"managed_credits_exhausted",
	"agent_budget_exhausted",
	"managed_upstream_unavailable",
	"agent_run_stopped",
	"future_agi_account_suspended",
	"org_budget_exceeded",
}

// ordinaryProviderCodes are a provider's ordinary codes.
var ordinaryProviderCodes = []string{
	"rate_limit_exceeded",
	"invalid_api_key",
	"model_not_found",
	"context_length_exceeded",
	"insufficient_quota",
	"provider_managed_credits_exhausted",
}

func reservedCodeBody(code string) []byte {
	return []byte(fmt.Sprintf(`{"error":{"message":"upstream said no","type":"invalid_request_error","code":%q}}`, code))
}

func keptProviderCode(e *models.APIError) any {
	meta, _ := e.Metadata.(map[string]any)
	return meta["provider_code"]
}

func TestAProviderCannotAnswerWithTheGatewaysOwnCode(t *testing.T) {
	for _, code := range reservedGatewayCodes {
		for _, status := range []int{400, 402, 403, 429} {
			e := parseProviderError(status, reservedCodeBody(code))
			if e.Code != "provider_error" {
				t.Errorf("%s (HTTP %d): code %q, want provider_error", code, status, e.Code)
			}
			if got := keptProviderCode(e); got != code {
				t.Errorf("%s (HTTP %d): provider_code %v, want %q", code, status, got, code)
			}
			if e.Message != "upstream said no" || e.ProviderStatus != status {
				t.Errorf("%s (HTTP %d): the rest of the answer changed: %+v", code, status, e)
			}
		}
	}
}

func TestAProvidersOwnCodePassesThroughUnchanged(t *testing.T) {
	for _, code := range ordinaryProviderCodes {
		e := parseProviderError(400, reservedCodeBody(code))
		if e.Code != code {
			t.Errorf("%s: code %q", code, e.Code)
		}
		if got := keptProviderCode(e); got != nil {
			t.Errorf("%s: provider_code %v set on an ordinary code", code, got)
		}
	}
}
