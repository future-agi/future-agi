package azure

// As in the openai adapter: a provider's error code that is one of the
// gateway's own refusal codes reaches the client as provider_error, with the
// provider's code kept in metadata.provider_code; every other code passes
// through unchanged.

import (
	"fmt"
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

var ordinaryProviderCodes = []string{
	"rate_limit_exceeded",
	"invalid_api_key",
	"model_not_found",
	"content_filter",
	"DeploymentNotFound",
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
		e := parseProviderError(400, reservedCodeBody(code))
		if e.Code != "provider_error" {
			t.Errorf("%s: code %q, want provider_error", code, e.Code)
		}
		if got := keptProviderCode(e); got != code {
			t.Errorf("%s: provider_code %v, want %q", code, got, code)
		}
	}
}

func TestAProvidersOwnCodePassesThroughUnchanged(t *testing.T) {
	for _, code := range ordinaryProviderCodes {
		e := parseProviderError(400, reservedCodeBody(code))
		if e.Code != code || keptProviderCode(e) != nil {
			t.Errorf("%s: code %q, provider_code %v", code, e.Code, keptProviderCode(e))
		}
	}
}
