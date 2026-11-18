package injection

import (
	"context"
	"testing"
)

func TestConfig1602InjectionReadiness(t *testing.T) {
	cases := []struct {
		name                  string
		cfg                   map[string]interface{}
		invalid               bool
		categories, threshold int
	}{
		{"defaults", nil, false, 6, 3},
		{"empty", map[string]interface{}{"categories": []interface{}{}}, false, 6, 3},
		{"valid", map[string]interface{}{"categories": []interface{}{"instruction_override", "encoding_bypass"}, "sensitivity": "HIGH"}, false, 2, 1},
		{"low", map[string]interface{}{"sensitivity": "LoW"}, false, 6, 5},
		{"medium", map[string]interface{}{"sensitivity": "MeDiUm"}, false, 6, 3},
		{"extra", map[string]interface{}{"future_option": map[string]interface{}{"retained": true}}, false, 6, 3},
		{"nonstring", map[string]interface{}{"categories": []interface{}{42}}, true, 0, 0},
		{"unknown", map[string]interface{}{"categories": []interface{}{"not-a-category"}}, true, 0, 0},
		{"mixed", map[string]interface{}{"categories": []interface{}{"instruction_override", 42}}, true, 0, 0},
		{"mixed-unknown", map[string]interface{}{"categories": []interface{}{"instruction_override", "not-a-category"}}, true, 0, 0},
		{"category-case", map[string]interface{}{"categories": []interface{}{"Instruction_override"}}, true, 0, 0},
		{"category-space", map[string]interface{}{"categories": []interface{}{" instruction_override"}}, true, 0, 0},
		{"category-null", map[string]interface{}{"categories": nil}, true, 0, 0},
		{"category-string", map[string]interface{}{"categories": "instruction_override"}, true, 0, 0},
		{"category-native-slice", map[string]interface{}{"categories": []string{"instruction_override"}}, true, 0, 0},
		{"sensitivity-null", map[string]interface{}{"sensitivity": nil}, true, 0, 0},
		{"sensitivity-number", map[string]interface{}{"sensitivity": 1}, true, 0, 0},
		{"sensitivity-unknown", map[string]interface{}{"sensitivity": "maximum"}, true, 0, 0},
		{"sensitivity-empty", map[string]interface{}{"sensitivity": ""}, true, 0, 0},
		{"sensitivity-space", map[string]interface{}{"sensitivity": " high"}, true, 0, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(tc.cfg)
			if (g.ValidateConfig() != nil) != tc.invalid {
				t.Fatalf("readiness error = %v; invalid = %v", g.ValidateConfig(), tc.invalid)
			}
			if !tc.invalid && (len(g.detectors) != tc.categories || g.threshold != tc.threshold) {
				t.Fatalf("constructor defaults changed: categories=%d threshold=%d", len(g.detectors), g.threshold)
			}
			// Benign input only: readiness must not alter legacy Check behavior.
			result := g.Check(context.Background(), makeInput("Please summarize the weather forecast."))
			if result.Err != nil || !result.Pass || result.Score != 0 {
				t.Fatalf("benign legacy check changed: %+v", result)
			}
		})
	}
}

func TestConfig1602InjectionActualCatalog(t *testing.T) {
	catalog := []string{"structured_role_injection", "instruction_override", "role_manipulation", "system_prompt_extraction", "delimiter_injection", "encoding_bypass"}
	if len(defaultDetectors()) != len(catalog) {
		t.Fatal("catalog parity changed")
	}
	for _, name := range catalog {
		g := New(map[string]interface{}{"categories": []interface{}{name, name}})
		if g.ValidateConfig() != nil || len(g.detectors) != 1 || g.detectors[0].category != name {
			t.Fatalf("actual catalog selector not supported: %s", name)
		}
	}
}

func TestConfig1602InjectionOriginalInputSnapshot(t *testing.T) {
	cfg := map[string]interface{}{"sensitivity": "unknown"}
	g := New(cfg)
	cfg["sensitivity"] = "medium"
	if g.ValidateConfig() == nil {
		t.Fatal("original invalid input silently normalized")
	}
	valid := New(cfg)
	cfg["sensitivity"] = "unknown"
	if valid.ValidateConfig() != nil {
		t.Fatal("caller mutation changed constructor readiness")
	}
}
