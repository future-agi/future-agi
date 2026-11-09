package server

import "testing"

// TestCallerCannotForgeRoutingEvidence: the request-log metadata keys G3 writes
// as routing evidence (routing_options, upstream_provider_object) are
// gateway-owned, so a caller cannot set them through x-agentcc-metadata or the
// body's metadata (G3 IR-3). On a BYOK or non-OpenRouter request G3 does not
// overwrite them, so a forged value would reach the org's log as evidence.
func TestCallerCannotForgeRoutingEvidence(t *testing.T) {
	for _, key := range []string{metaRoutingOptions, metaUpstreamProviderObject, "Routing_Options", "UPSTREAM_PROVIDER_OBJECT"} {
		if !isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is not blocked; it is gateway-written routing evidence", key)
		}
	}
	for _, key := range []string{"routing", "routing_team", "upstream", "provider"} {
		if isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is blocked; it is an ordinary dimension", key)
		}
	}
}
