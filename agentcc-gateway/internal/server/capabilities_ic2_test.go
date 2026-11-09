package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// IC-2 (W2b integration, C200, C212): capabilities-v1 takes what SR1 has V5
// emit in W2c: managed_resale_terms native and byok_only, an optional price
// object on a provider row, and the fetch, documents.parse, guardrails,
// evals and browser.sessions endpoint rows. The integrator's instance in
// gates/fixtures/W2b validates, the existing golden still does (G8's
// TestCapabilitiesGolden is unedited), and a value outside the new enums or
// an incomplete price is still refused.
func TestCapabilitiesSchemaAcceptsSR1Instance(t *testing.T) {
	_, here, _, _ := runtime.Caller(0)
	path := filepath.Join(filepath.Dir(here), "..", "..", "scripts", "scenarios", "gates", "fixtures", "W2b", "capabilities-sr1.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var instance map[string]any
	if err := json.Unmarshal(raw, &instance); err != nil {
		t.Fatal(err)
	}
	if errs := schematest.Validate(t, "capabilities-v1.schema.json", instance); len(errs) > 0 {
		t.Fatalf("the SR1 instance fails capabilities-v1:\n%s", strings.Join(errs, "\n"))
	}

	mutate := func(edit func(row map[string]any)) map[string]any {
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		row := m["endpoints"].([]any)[0].(map[string]any)["providers"].(map[string]any)["exa"].(map[string]any)
		edit(row)
		return m
	}
	for name, bad := range map[string]map[string]any{
		"unknown resale terms": mutate(func(r map[string]any) { r["managed_resale_terms"] = "resold" }),
		"price without sku":    mutate(func(r map[string]any) { delete(r["price"].(map[string]any), "sku") }),
		"negative price":       mutate(func(r map[string]any) { r["price"].(map[string]any)["usd_per_unit_billed"] = -1 }),
		"extra price field":    mutate(func(r map[string]any) { r["price"].(map[string]any)["currency"] = "usd" }),
	} {
		if errs := schematest.Validate(t, "capabilities-v1.schema.json", bad); len(errs) == 0 {
			t.Errorf("%s: validated, want refused", name)
		}
	}
}
