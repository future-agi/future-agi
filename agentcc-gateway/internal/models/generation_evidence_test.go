package models

import (
	"encoding/json"
	"testing"
)

func TestRouterCostDecimalPreservesOriginalEvidence(t *testing.T) {
	var u Usage
	const exact = "0.00000949999999999999999"
	if err := json.Unmarshal([]byte(`{"cost":`+exact+`}`), &u); err != nil || u.CostDecimal != exact {
		t.Fatalf("decimal=%q error=%v", u.CostDecimal, err)
	}
	billed := 0.000011
	u.Cost = &billed // customer-facing rewrite must not corrupt provider evidence
	if u.CostDecimal != exact {
		t.Fatal("rewrote upstream cost evidence")
	}
	for _, raw := range []string{`null`, `-1`, `"invalid"`, `1e999`, `{}`} {
		var invalid Usage
		if err := json.Unmarshal([]byte(`{"cost":`+raw+`,"prompt_tokens":2}`), &invalid); err != nil || invalid.CostDecimal != raw || invalid.HasReportedCost() {
			t.Fatalf("invalid cost must retain the generation, not a price: raw=%s usage=%+v err=%v", raw, invalid, err)
		}
	}
	var empty Usage
	if err := json.Unmarshal([]byte(`{}`), &empty); err != nil {
		t.Fatal(err)
	}
	merged := MergeUsage(&u, &empty)
	if merged.CostDecimal != exact {
		t.Fatal("empty final usage erased decimal")
	}
	var invalid Usage
	if err := json.Unmarshal([]byte(`{"cost":-1}`), &invalid); err != nil {
		t.Fatal(err)
	}
	// The pair stays together, and the explicit invalid terminal cost contests
	// it: inclusive settlement charges the full hold, never the earlier partial.
	if got := MergeUsage(&u, &invalid); got.CostDecimal != exact || *got.Cost != billed || !got.CostContested() {
		t.Fatal("invalid terminal cost silently reused earlier partial cost")
	}
	if MergeUsage(&u, &empty).CostContested() {
		t.Fatal("an omitted final cost contested the total")
	}
}

func TestRouterGenerationConflictsAreNotSilentlyOverwritten(t *testing.T) {
	identity := GenerationEvidence{ID: "gen-one", ServedModel: "model-one"}
	identity.Observe(GenerationEvidence{})
	if identity.ID != "gen-one" || identity.Conflict {
		t.Fatal(identity)
	}
	identity.Observe(GenerationEvidence{ID: "gen-two", ServedModel: "model-two"})
	if identity.ID != "gen-two" || identity.ServedModel != "model-two" || !identity.Conflict {
		t.Fatal(identity)
	}
	identity.Observe(GenerationEvidence{})
	if !identity.Conflict {
		t.Fatal("empty terminal event cleared conflict")
	}
}
