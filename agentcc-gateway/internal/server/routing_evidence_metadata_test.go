package server

import "testing"

// TestCallerCannotForgeRoutingEvidence: the request-log metadata keys the
// gateway writes as routing evidence (routing_options,
// upstream_provider_object) are gateway-owned, so a caller cannot set them
// through x-agentcc-metadata or the body's metadata.
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
