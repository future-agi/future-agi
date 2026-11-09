package catalog

import "testing"

func TestOwnedProfileRequiresExplicitActivation(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		c := &Catalog{byProvider: map[string]*Snapshot{"openrouter": {Models: map[string]*Model{
			"token":     {ID: "token"},
			"inclusive": {ID: "inclusive", InclusiveEnabled: enabled},
		}}}}
		for i := 0; i < 2; i++ {
			if got := c.OwnsManagedAttempts(); got != enabled {
				t.Fatalf("enabled=%t owned=%t", enabled, got)
			}
		}
	}
	var missing *Catalog
	if missing.OwnsManagedAttempts() {
		t.Fatal("missing catalogue activated ownership")
	}
}
