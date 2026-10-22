package billing

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// schemaRequired is managed-settlement-v1's required field list, from its
// schema in api_contracts/gateway/examples.
func schemaRequired(t *testing.T) []string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "managed-settlement-v1.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Required []string `json:"required"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	sort.Strings(schema.Required)
	return schema.Required
}

func TestSettlementFieldsAreTheSchemasFields(t *testing.T) {
	rec := Settlement{Kind: KindSpend, RequestID: "req-1", OrgID: "org", BilledMicros: 11815, UpstreamMicros: 11252,
		MarkupMicros: 563, MarkupBps: 500, HoldMicros: 40000, Attempts: []AttemptSummary{{AttemptID: "req-1.1",
			Kind: "primary", Provider: "openrouter", Model: "m", CredentialType: "managed", UpstreamMicros: 11252,
			BilledMicros: 11815, Outcome: "success"}}, TsMs: 1792056612419}
	f := rec.Fields()
	var keys []string
	for i := 0; i < len(f); i += 2 {
		keys = append(keys, f[i])
	}
	sort.Strings(keys)
	want := schemaRequired(t)
	if len(keys) != len(want) {
		t.Fatalf("record fields %v, want %v", keys, want)
	}
	for i := range keys {
		if keys[i] != want[i] {
			t.Fatalf("record fields %v, want %v", keys, want)
		}
	}
	m := rec.FieldMap()
	if m["generation_id"] != "req-1" || m["cost_source"] != "unpriced" || m["usage_estimated"] != "0" || m["v"] != "1" {
		t.Errorf("defaults: %v", m)
	}
	var attempts []map[string]any
	if err := json.Unmarshal([]byte(m["attempts"]), &attempts); err != nil || len(attempts) != 1 || attempts[0]["billed_micros"] != 11815.0 {
		t.Errorf("attempts = %s (%v)", m["attempts"], err)
	}
	if empty := (Settlement{}).FieldMap()["attempts"]; empty != "[]" {
		t.Errorf("no attempts = %q, want []", empty)
	}
}
