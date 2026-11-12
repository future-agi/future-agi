package anthropic

import "testing"

// DX-ERRORS-1: an error answer with no Anthropic error type (an
// OpenAI-shaped body from a provider saved with the wrong api_format, say)
// is coded by its HTTP status, never the bare "provider_" that names
// nothing.
func TestParseAnthropicErrorWithoutTypeIsCodedByStatus(t *testing.T) {
	apiErr := parseAnthropicError(404, []byte(`{"error":{"message":"no route /v1/messages"}}`))
	if apiErr.Code != "provider_404" || apiErr.Message != "no route /v1/messages" {
		t.Fatalf("code %q message %q; want provider_404 with the provider's message", apiErr.Code, apiErr.Message)
	}
}
