package modeldb

import "testing"

func TestAllZeroTiersDeclareFree(t *testing.T) {
	// Positive tier boundaries describe quantities, not charges.
	raw := []byte(`{"input_cost_per_token":0,"output_cost_per_token":0,"tiered_pricing":[{"range":[0,200000],"input_cost_per_token":0,"output_cost_per_token":0},{"range":[200000,500000],"cache_read_input_token_cost":0,"output_cost_per_token":0}]}`)
	if !declaresFree(raw, nil) {
		t.Fatal("expected catalogue for zero prices in every tier")
	}
}
