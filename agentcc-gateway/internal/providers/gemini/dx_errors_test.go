package gemini

import "testing"

// DX-ERRORS-1: an error answer with no RPC status is coded by its HTTP
// status, never the bare "provider_" that names nothing.
func TestParseGeminiErrorWithoutStatusIsCodedByHTTPStatus(t *testing.T) {
	apiErr := parseGeminiError(404, []byte(`{"error":{"code":404,"message":"no such route"}}`))
	if apiErr.Code != "provider_404" || apiErr.Message != "no such route" {
		t.Fatalf("code %q message %q; want provider_404 with the provider's message", apiErr.Code, apiErr.Message)
	}
}
