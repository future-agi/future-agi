package openai

import "testing"

func TestRouterHTTPErrorKeepsBillingEvidence(t *testing.T) {
	failure := parseProviderError(502, []byte(`{"error":{"message":"synthetic failure","type":"upstream_error","code":"failed"},"id":"gen-http","model":"test/served","provider":"Synthetic","usage":{"cost":0.00000949999999999999999}}`))
	if failure == nil || failure.BillingEvidence == nil || failure.BillingEvidence.Generation.ID != "gen-http" || failure.BillingEvidence.Generation.ServedModel != "test/served" || failure.BillingEvidence.Usage.CostDecimal != "0.00000949999999999999999" {
		t.Fatalf("HTTP error evidence=%+v", failure)
	}
}

func TestRouterSSEErrorCarriesEvidenceOnBothChannels(t *testing.T) {
	failure, chunk := streamErrorEnvelope([]byte(`{"error":{"message":"synthetic failure","code":502},"id":"gen-sse","model":"test/served","usage":{"cost":"invalid"}}`))
	if failure == nil || failure.BillingEvidence == nil || failure.BillingEvidence.Generation.ID != chunk.ID || chunk.ID != "gen-sse" || failure.BillingEvidence.Usage.CostDecimal != `"invalid"` || chunk.Usage.HasReportedCost() {
		t.Fatalf("terminal error lost identity or accepted invalid cost: failure=%+v chunk=%+v", failure, chunk)
	}
}
