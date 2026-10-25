package server

import "testing"

// TestGatewayOwnedMetadataKeysAreBlocked: billing_alert (an unpriced
// managed hold), source_choice (written only when a model's managed sources
// collapse) and outcome_class are request-log metadata keys the gateway
// writes, so x-agentcc-metadata and the body's metadata never set them.
// Ordinary dimensions with similar names stay open.
func TestGatewayOwnedMetadataKeysAreBlocked(t *testing.T) {
	for _, key := range []string{"billing_alert", "Billing_Alert", metaSourceChoice, "SOURCE_CHOICE", "outcome_class", "Outcome_Class"} {
		if !isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is not blocked; it is gateway-written", key)
		}
	}
	for _, key := range []string{"billing", "source", "outcome", "choice", "alert"} {
		if isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is blocked; it is an ordinary dimension", key)
		}
	}
}
