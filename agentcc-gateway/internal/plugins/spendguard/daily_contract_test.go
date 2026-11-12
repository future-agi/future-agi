package spendguard

import (
	"encoding/json"
	"os"
	"slices"
	"testing"
)

// These value-free reason variants are exceptions to the usual scope and
// reset metadata. Keep both conditional branches and the public enum aligned.
// The branches are found by what they say, never by their place in
// error.allOf: db4e8424 reordered allOf and left the reasons as they were.
func TestNewOrgDailyReasonContract(t *testing.T) {
	data, err := os.ReadFile("../../../../api_contracts/gateway/examples/error-402-v1.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(data, &schema); err != nil {
		t.Fatal(err)
	}
	// at walks keys through nested objects; ok is false when a key is missing
	// or a value on the way is not an object.
	at := func(value any, keys ...string) (any, bool) {
		for _, key := range keys {
			object, isObject := value.(map[string]any)
			if !isObject {
				return nil, false
			}
			next, found := object[key]
			if !found {
				return nil, false
			}
			value = next
		}
		return value, true
	}
	// array is the array at keys, or nil when there is none.
	array := func(value any, keys ...string) []any {
		v, ok := at(value, keys...)
		if !ok {
			return nil
		}
		items, _ := v.([]any)
		return items
	}
	reasonEnum := func(clause any) []any {
		return array(clause, "properties", "metadata", "properties", "reason", "enum")
	}

	global := array(schema, "$defs", "Metadata", "properties", "reason", "enum")
	if global == nil {
		t.Fatal("$defs.Metadata.properties.reason.enum is missing or not an array")
	}
	rules := array(schema, "properties", "error", "allOf")
	if rules == nil {
		t.Fatal("properties.error.allOf is missing or not an array")
	}
	// allowReasonOnly: the if/anyOf alternative that names the reasons, so a
	// refusal carrying one of them need not carry limit_source and the rest.
	// valueFree: the rule whose if names the reasons; its then keeps the
	// metadata to the reason alone.
	var allowReasonOnly, valueFree []any
	var valueFreeRule any
	for i, rule := range rules {
		for _, alternative := range array(rule, "if", "anyOf") {
			if enum := reasonEnum(alternative); enum != nil {
				if allowReasonOnly != nil {
					t.Fatalf("error.allOf[%d]: a second if/anyOf branch names metadata.reason", i)
				}
				allowReasonOnly = enum
			}
		}
		if condition, ok := at(rule, "if"); ok {
			if enum := reasonEnum(condition); enum != nil {
				if valueFree != nil {
					t.Fatalf("error.allOf[%d]: a second rule's if names metadata.reason", i)
				}
				valueFree, valueFreeRule = enum, rule
			}
		}
	}
	if allowReasonOnly == nil {
		t.Fatal("no error.allOf rule has an if/anyOf branch on metadata.reason")
	}
	if valueFree == nil {
		t.Fatal("no error.allOf rule has an if on metadata.reason")
	}
	for _, reason := range []string{"request_exceeds_minute_cap", "request_exceeds_hour_cap", "new_org_daily_cap"} {
		if !slices.Contains(global, any(reason)) {
			t.Fatalf("public reason enum omits %s", reason)
		}
		if !slices.Contains(allowReasonOnly, any(reason)) {
			t.Fatalf("value-free contract branch (if/anyOf) omits %s", reason)
		}
		if !slices.Contains(valueFree, any(reason)) {
			t.Fatalf("value-free contract branch (if/properties) omits %s", reason)
		}
	}
	if limit, ok := at(valueFreeRule, "then", "properties", "metadata", "maxProperties"); !ok || limit != float64(1) {
		t.Fatalf("value-free refusal must allow only the reason: then.properties.metadata.maxProperties = %v, want 1", limit)
	}
}
