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
// The conditionals are found by what they say, never by their place in
// error.allOf: db4e8424 reordered allOf (TestNewOrgDailyReasonContract finds
// them the same way).
func checkReasonOnlyContract(t *testing.T, raw []byte, body errorBody) {
	t.Helper()
	var schema map[string]any
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	// lookup walks keys through nested objects; ok is false when a key is
	// missing or a value on the way is not an object.
	lookup := func(value any, keys ...string) (any, bool) {
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
	at := func(value any, keys ...string) any {
		value, _ = lookup(value, keys...)
		return value
	}
	contains := func(value, want any) bool {
		values, ok := value.([]any)
		return ok && slices.Contains(values, want)
	}
	namesReason := func(clause any) bool {
		_, ok := lookup(clause, "properties", "metadata", "properties", "reason", "enum")
		return ok
	}
	reason := body.Error.Metadata["reason"]
	if !contains(at(schema, "$defs", "Metadata", "properties", "reason", "enum"), reason) {
		t.Fatalf("reason %v is not in error-402-v1's enum", reason)
	}
	rules, ok := at(schema, "properties", "error", "allOf").([]any)
	if !ok || len(rules) == 0 {
		t.Fatal("missing error-402-v1 conditional branches")
	}
	// exception: the if/anyOf alternative that names the reason, so a refusal
	// with it need not carry limit_source and the rest (exceptionRule holds
	// it). valueFreeRule: the rule whose if names the reason; its then keeps
	// the metadata to the reason alone.
	var exceptionRule, exception, valueFreeRule any
	for i, rule := range rules {
		alternatives, _ := at(rule, "if", "anyOf").([]any)
		for _, alternative := range alternatives {
			if namesReason(alternative) {
				if exception != nil {
					t.Fatalf("error.allOf[%d]: a second if/anyOf branch names metadata.reason", i)
				}
				exceptionRule, exception = rule, alternative
			}
		}
		if condition, found := lookup(rule, "if"); found && namesReason(condition) {
			if valueFreeRule != nil {
				t.Fatalf("error.allOf[%d]: a second rule's if names metadata.reason", i)
			}
			valueFreeRule = rule
		}
	}
	if exception == nil {
		t.Fatal("missing reason-only exception to the limit metadata requirement")
	}
	// The exception has exactly two alternatives: the codes that carry no
	// limit metadata, and the reason-only refusals. A third would let more
	// refusals skip limit_source and the rest.
	if alternatives, _ := at(exceptionRule, "if", "anyOf").([]any); len(alternatives) != 2 {
		t.Fatalf("the limit-metadata exception has %d if.anyOf alternatives, want exactly 2", len(alternatives))
	}
	if valueFreeRule == nil {
		t.Fatal("missing the reason-only error-402-v1 branch (an if on metadata.reason)")
	}
	for _, clause := range []any{exception, at(valueFreeRule, "if")} {
		metadata := at(clause, "properties", "metadata")
		if !contains(at(metadata, "required"), "reason") ||
			!contains(at(metadata, "properties", "reason", "enum"), reason) {
			t.Fatalf("schema conditional does not require and admit reason %v", reason)
		}
	}
	if !contains(at(exceptionRule, "then", "properties", "metadata", "not", "required"), "limit_source") {
		t.Fatal("reason-only exception must forbid limit_source")
	}
	if at(valueFreeRule, "then", "properties", "code", "const") != body.Error.Code ||
		at(valueFreeRule, "then", "properties", "metadata", "maxProperties") != float64(1) || len(body.Error.Metadata) != 1 {
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
