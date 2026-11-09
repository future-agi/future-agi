package spendguard

import (
	"encoding/json"
	"os"
	"slices"
	"testing"
)

// These value-free reason variants are exceptions to the usual scope and
// reset metadata. Keep both conditional branches and the public enum aligned.
func TestNewOrgDailyReasonContract(t *testing.T) {
	data, err := os.ReadFile("../../../../api_contracts/gateway/examples/error-402-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(data, &schema); err != nil {
		t.Fatal(err)
	}
	at := func(value any, keys ...string) any {
		for _, key := range keys {
			value = value.(map[string]any)[key]
		}
		return value
	}
	global := at(schema, "$defs", "Metadata", "properties", "reason", "enum").([]any)
	rules := at(schema, "properties", "error", "allOf").([]any)
	allowReasonOnly := at(rules[0], "if", "anyOf").([]any)[1]
	for _, reason := range []string{"request_exceeds_minute_cap", "request_exceeds_hour_cap", "new_org_daily_cap"} {
		if !slices.Contains(global, any(reason)) {
			t.Fatalf("public reason enum omits %s", reason)
		}
		for _, clause := range []any{allowReasonOnly, at(rules[2], "if")} {
			enum, ok := at(clause, "properties", "metadata", "properties", "reason").(map[string]any)["enum"].([]any)
			if !ok || !slices.Contains(enum, any(reason)) {
				t.Fatalf("value-free contract branch omits %s", reason)
			}
		}
	}
	if at(rules[2], "then", "properties", "metadata", "maxProperties") != float64(1) {
		t.Fatal("value-free refusal must allow only the reason")
	}
}
