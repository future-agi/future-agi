package observedcatalog

import (
	"testing"
	"time"
)

func TestExtractSpanSkipsPhase0PromotedSentinels(t *testing.T) {
	span := ScopedSpan{
		OrganizationID: "11111111-1111-4111-8111-111111111111",
		WorkspaceID:    "22222222-2222-4222-8222-222222222222",
		Row: map[string]any{
			"project_id":   "33333333-3333-4333-8333-333333333333",
			"start_time":   time.Date(2026, 11, 21, 12, 0, 0, 0, time.UTC).Format("2006-01-02 15:04:05.000000"),
			"model":        "gpt-4o",
			"attrs_string": map[string]string{"env": "prod"},
			"attrs_number": map[string]float64{},
			"attrs_bool":   map[string]uint8{},
			"attributes_extra": map[string]any{
				"__fi_promoted_input_key":   "input.value",
				"__fi_promoted_input_json":  true,
				"__fi_promoted_output_key":  "output.value",
				"__fi_promoted_output_json": false,
				"custom_tags":               []any{"billing", "checkout"},
			},
		},
	}

	batch, _, err := Extract(span, DefaultLimits())
	if err != nil {
		t.Fatalf("ExtractSpan: %v", err)
	}
	for _, k := range batch.Keys {
		if k.AttributeKey == "__fi_promoted_input_key" ||
			k.AttributeKey == "__fi_promoted_input_json" ||
			k.AttributeKey == "__fi_promoted_output_key" ||
			k.AttributeKey == "__fi_promoted_output_json" {
			t.Fatalf("internal Phase 0 sentinel leaked into observed catalog keys: %+v", k)
		}
	}
	for _, v := range batch.Values {
		if v.AttributeKey == "__fi_promoted_input_key" || v.AttributeKey == "__fi_promoted_output_key" {
			t.Fatalf("internal Phase 0 sentinel leaked into observed catalog values: %+v", v)
		}
	}
}
