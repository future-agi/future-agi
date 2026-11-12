package toolcatalog

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

const cardSchema = "tool-price-card-v1.schema.json"

// The loader reads exactly the tool-price-card-v1 shape: every card of the
// golden parses, and a card the schema refuses is refused here too, so the
// two cannot drift apart.
func TestPriceCardSchema(t *testing.T) {
	golden := schematest.ReadExample(t, "tool-price-card-v1.json")["cards"].([]any)
	for _, raw := range golden {
		card := raw.(map[string]any)
		if _, err := ParseCard(mustJSON(t, card)); err != nil {
			t.Errorf("golden card %s/%s does not parse: %v", card["capability"], card["vendor"], err)
		}
	}
	if errs := schematest.ValidateDef(t, cardSchema, "Card", validCard()); len(errs) > 0 {
		t.Fatalf("the test card fails the schema: %v", errs)
	}
	sku := func(k string, v any) map[string]any {
		s := validCard()["skus"].([]any)[0].(map[string]any)
		return with(validCard(), "skus", []any{with(s, k, v)})
	}
	refused := map[string]map[string]any{
		"unknown card field":        with(validCard(), "managed", true),
		"unknown resale status":     with(validCard(), "resale_status", "resold"),
		"no skus":                   with(validCard(), "skus", []any{}),
		"empty vendor":              with(validCard(), "vendor", ""),
		"string flag":               with(validCard(), "no_divert", "false"),
		"bad cacheable":             with(validCard(), "cacheable", "forever"),
		"bad retention":             with(validCard(), "results_retention", "all"),
		"bad eval export":           with(validCard(), "eval_export", "maybe"),
		"bad date":                  with(validCard(), "price_effective_until", "soon"),
		"empty region":              with(validCard(), "regions", []any{""}),
		"regions missing":           with(validCard(), "regions", deleteKey),
		"unknown unit":              sku("unit", "hour"),
		"negative price":            sku("usd_per_unit", -0.01),
		"price as string":           sku("usd_per_unit", "0.01"),
		"unknown sku field":         sku("per_call_usd", 0.01),
		"sku without free_on_fail":  sku("free_on_failure", deleteKey),
		"negative max_usd_per_call": sku("max_usd_per_call", -1),
	}
	for name, card := range refused {
		if errs := schematest.ValidateDef(t, cardSchema, "Card", card); len(errs) == 0 {
			t.Errorf("%s: the schema accepts it, so the case does not test the shape", name)
		}
		if _, err := ParseCard(mustJSON(t, card)); err == nil {
			t.Errorf("%s: the schema refuses it but ParseCard accepts it", name)
		}
	}
}

// Every terms flag is required and typed: a card that leaves one out, or
// sets it to null, is refused with the flag's name.
func TestTermsFlagsRequired(t *testing.T) {
	for _, flag := range []string{"no_divert", "no_perf_disclosure", "no_benchmark", "improvement_license",
		"trains_on_input", "zdr_capable", "results_retention", "cacheable", "eval_export", "resale_status", "resale_terms_ref"} {
		for name, card := range map[string]map[string]any{
			"missing": with(validCard(), flag, deleteKey),
			"null":    with(validCard(), flag, nil),
		} {
			_, err := ParseCard(mustJSON(t, card))
			if err == nil || !strings.Contains(err.Error(), flag) {
				t.Errorf("%s %s: got %v, want an error naming it", name, flag, err)
			}
		}
	}
	for _, v := range []string{"maybe", "", "No"} {
		if _, err := ParseCard(mustJSON(t, with(validCard(), "trains_on_input", v))); err == nil {
			t.Errorf("trains_on_input %q accepted; it has three values: no, ai_features_only, yes", v)
		}
	}
	for _, v := range []string{"no", "ai_features_only", "yes"} {
		if _, err := ParseCard(mustJSON(t, with(validCard(), "trains_on_input", v))); err != nil {
			t.Errorf("trains_on_input %q refused: %v", v, err)
		}
	}
}

// A card records when its terms were read: the date is required and must be
// a real calendar date, and every seed card carries one that is not in the
// future.
func TestTermsCheckedAtPresent(t *testing.T) {
	for _, v := range []any{deleteKey, nil, "", "2026-13-40", "2026-02-30", "15 Oct 2026", "2026-10-15T00:00:00Z"} {
		if _, err := ParseCard(mustJSON(t, with(validCard(), "terms_checked_at", v))); err == nil ||
			!strings.Contains(err.Error(), "terms_checked_at") {
			t.Errorf("terms_checked_at %v: got %v, want an error naming it", v, err)
		}
	}
	for _, c := range seedCatalog(t).Cards() {
		at, err := parseDate(c.TermsCheckedAt)
		if err != nil {
			t.Errorf("seed %s/%s: terms_checked_at %q: %v", c.Capability, c.Vendor, c.TermsCheckedAt, err)
			continue
		}
		if at.After(today()) {
			t.Errorf("seed %s/%s: terms_checked_at %s is in the future", c.Capability, c.Vendor, c.TermsCheckedAt)
		}
		if !strings.HasPrefix(c.TermsURL, "https://") {
			t.Errorf("seed %s/%s: terms_url %q is not an https URL", c.Capability, c.Vendor, c.TermsURL)
		}
	}
}

// The resale status and the terms ref agree: managed_partner has
// a ref, unconfirmed and byok_only have none, a card whose price is pending
// prices every SKU at 0, and a card a managed call can be admitted at is not
// free unless its price is pending or comes from the vendor's result.
func TestResaleStatusConsistent(t *testing.T) {
	free := func(card map[string]any, extra map[string]any) map[string]any {
		s := map[string]any{"sku": "acme.search", "unit": "request", "usd_per_unit": 0.0, "free_on_failure": true}
		for k, v := range extra {
			s[k] = v
		}
		return with(card, "skus", []any{s})
	}
	partner := with(with(validCard(), "resale_status", "managed_partner"), "resale_terms_ref", "legal/acme.pdf")
	for name, card := range map[string]map[string]any{
		"unconfirmed at $0":     free(validCard(), nil),
		"managed_partner at $0": free(partner, nil),
		"partner without ref":   with(validCard(), "resale_status", "managed_partner"),
		"unconfirmed with ref":  with(validCard(), "resale_terms_ref", "legal/acme.pdf"),
		"pending with a price":  with(validCard(), "owner_price_pending", true),
		"http terms url":        with(validCard(), "terms_url", "http://acme.example/terms"),
		"duplicate sku": with(validCard(), "skus", []any{
			map[string]any{"sku": "a", "unit": "request", "usd_per_unit": 0.01, "free_on_failure": true},
			map[string]any{"sku": "a", "unit": "page", "usd_per_unit": 0.01, "free_on_failure": true},
		}),
	} {
		if _, err := ParseCard(mustJSON(t, card)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	for name, card := range map[string]map[string]any{
		"managed_partner with its ref":  partner,
		"byok_only at $0":               free(with(validCard(), "resale_status", "byok_only"), nil),
		"native at $0":                  free(with(validCard(), "resale_status", "native"), nil),
		"unconfirmed, price pending":    free(with(validCard(), "owner_price_pending", true), nil),
		"unconfirmed, cost from result": free(validCard(), map[string]any{"cost_from_result_meta": true}),
	} {
		if _, err := ParseCard(mustJSON(t, card)); err != nil {
			t.Errorf("%s: refused: %v", name, err)
		}
	}
}
