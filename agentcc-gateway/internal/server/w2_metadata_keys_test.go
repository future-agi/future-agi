package server

import "testing"

// TestCallerCannotForgeW2Metadata (W2 integration; G6 IR-2, G10 IR-7, G18
// IR-2): the request-log metadata keys the W2 lanes write are gateway-owned,
// so a caller cannot set them through x-agentcc-metadata or the body's
// metadata: billing_alert (G6, an unpriced managed hold), source_choice (G10,
// written only when a model's managed sources collapse, so a forged value
// would otherwise reach the log on every other request) and outcome_class
// (G18). Ordinary dimensions with similar names stay open.
func TestCallerCannotForgeW2Metadata(t *testing.T) {
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
