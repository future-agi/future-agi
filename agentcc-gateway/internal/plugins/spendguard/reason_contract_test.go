package spendguard

import (
	"encoding/json"
	"net/http"
	"slices"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// checkReasonOnlyContract checks the two schema conditionals as well as the
// body. A missing exception or a widened metadata branch must fail this check,
// not silently exempt a refusal from the normal limit metadata requirements.
func checkReasonOnlyContract(t *testing.T, raw []byte, body errorBody) {
	t.Helper()
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	at := func(value any, keys ...string) any {
		t.Helper()
		for _, key := range keys {
			object, ok := value.(map[string]any)
			if !ok {
				t.Fatalf("contract path %v: %s is not in an object", keys, key)
			}
			value = object[key]
		}
		return value
	}
	contains := func(value, want any) bool {
		values, ok := value.([]any)
		return ok && slices.Contains(values, want)
	}
	reason := body.Error.Metadata["reason"]
	if !contains(at(schema, "$defs", "Metadata", "properties", "reason", "enum"), reason) {
		t.Fatalf("reason %v is not in error-402-v1's enum", reason)
	}
	rules, ok := at(schema, "properties", "error", "allOf").([]any)
	if !ok || len(rules) != 3 {
		t.Fatal("missing error-402-v1 conditional branches")
	}
	exceptions, ok := at(rules[0], "if", "anyOf").([]any)
	if !ok || len(exceptions) != 2 {
		t.Fatal("missing reason-only exception to the limit metadata requirement")
	}
	for _, clause := range []any{exceptions[1], at(rules[2], "if")} {
		metadata := at(clause, "properties", "metadata")
		if !contains(at(metadata, "required"), "reason") ||
			!contains(at(metadata, "properties", "reason", "enum"), reason) {
			t.Fatalf("schema conditional does not require and admit reason %v", reason)
		}
	}
	if !contains(at(rules[0], "then", "properties", "metadata", "not", "required"), "limit_source") {
		t.Fatal("reason-only exception must forbid limit_source")
	}
	if at(rules[2], "then", "properties", "code", "const") != body.Error.Code ||
		at(rules[2], "then", "properties", "metadata", "maxProperties") != float64(1) || len(body.Error.Metadata) != 1 {
		t.Fatalf("refusal does not match the reason-only schema branch: %+v", body.Error)
	}
}

func TestSpendGuardRequestExceedsMinuteCap(t *testing.T) {
	store, rdb, prefix := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{MaxUSDPerMinute: 0.01}, nil)
	w := writeErr(g.Admit(bg, "org-own", "key_own", 11_000, "oversized"))
	body := checkErrorContract(t, w)
	if w.Code != http.StatusTooManyRequests || body.Error.Code != codeVelocity ||
		body.Error.Metadata["reason"] != "request_exceeds_minute_cap" || w.Header().Get("Retry-After") != "" {
		t.Fatalf("own-request cap: %d %s", w.Code, w.Body)
	}
	if got := bucket(t, rdb, orgMinute(prefix, "org-own", t0)); got != 0 {
		t.Fatalf("refused request counted %d micros", got)
	}
	if apiErr := g.Admit(bg, "org-own", "key_own", 10_000, "fits"); apiErr != nil {
		t.Fatalf("request at the minute cap: %v", apiErr)
	}
}
