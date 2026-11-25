package contentmod

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This exact JSON is also exercised by the Python and JavaScript parity tests.
const numericBoundaryJSON = `{"categories":["toxicity","hate_speech","violence","sexual","self_harm"],"category_threshold":3,"weights":{"toxicity":1.7976931348623157e308,"hate_speech":4.9896007738368e291,"violence":4.9896007738368e291,"sexual":4.9896007738368e291,"self_harm":4.9896007738368e291}}`

func numericBoundaryConfig(t *testing.T) map[string]interface{} {
	t.Helper()
	var cfg map[string]interface{}
	if err := json.Unmarshal([]byte(numericBoundaryJSON), &cfg); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func categoryNames(g *ContentModerationGuardrail) []string {
	names := make([]string, len(g.categories))
	for i, cat := range g.categories {
		names[i] = cat.name
	}
	return names
}

func TestNumericReadinessSharedParityFixtures(t *testing.T) {
	data, err := os.ReadFile("testdata/numeric_readiness.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name      string                 `json:"name"`
		Config    map[string]interface{} `json:"config"`
		Threshold float64                `json:"threshold"`
		Valid     bool                   `json:"valid"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			before, _ := json.Marshal(tc.Config)
			g := New(tc.Config).SelectedPolicyGuardrail().(*ContentModerationGuardrail)
			err := g.ValidateConfig()
			if err == nil {
				err = g.ValidateSelectedThreshold(tc.Threshold)
			}
			if (err == nil) != tc.Valid {
				t.Fatalf("selected error=%v want valid=%v", err, tc.Valid)
			}
			after, _ := json.Marshal(tc.Config)
			if string(before) != string(after) {
				t.Fatal("readiness mutated caller configuration")
			}
		})
	}
}

func TestNumericReadinessBoundaryArithmetic(t *testing.T) {
	// Deterministic arithmetic witness, independent of Go's map iteration.
	small := math.Ldexp(1, 969)
	if small != 4.9896007738368e291 {
		t.Fatal("boundary fixture changed")
	}
	largeFirst := math.MaxFloat64
	var smallFirst float64
	for i := 0; i < 4; i++ {
		largeFirst += small
		smallFirst += small
	}
	smallFirst += math.MaxFloat64
	if largeFirst != math.MaxFloat64 || !math.IsInf(smallFirst, 1) {
		t.Fatalf("boundary lost: large-first=%g small-first=%g", largeFirst, smallFirst)
	}
	t.Logf("large-first=%g small-first=%g", largeFirst, smallFirst)
}

func TestNumericReadinessActualConstructor(t *testing.T) {
	// Bounded real constructors, not injected ordering or a copied validator.
	// The arithmetic test above is deterministic; this loop observes actual
	// legacy orders and catches the original cached error without forcing them.
	orders := map[string]int{}
	invalid := 0
	for i := 0; i < 128; i++ {
		g := New(numericBoundaryConfig(t))
		order := strings.Join(categoryNames(g), ",")
		orders[order]++
		selected := g.SelectedPolicyGuardrail().(*ContentModerationGuardrail)
		if err := g.ValidateConfig(); err != nil {
			invalid++
			t.Logf("constructor=%d legacy_order=%s original_config_error=%v", i, order, err)
			continue
		}
		if err := selected.ValidateSelectedThreshold(.8); err != nil {
			t.Errorf("constructor=%d selected readiness: %v", i, err)
		}
		if result := selected.Check(context.Background(), makeInput("The weather is pleasant today.")); !result.Pass || result.Err != nil || result.Score != 0 {
			t.Fatalf("safe input did not evaluate: %+v", result)
		}
		if result := selected.Check(context.Background(), makeInput("you are stupid; you're an idiot; shut up")); result.Err != nil || result.Score != 1 || result.Pass {
			t.Fatalf("selected boundary score must be finite and blocking: %+v", result)
		}
	}
	t.Logf("actual_constructors=128 distinct_legacy_orders=%d orders=%v rejected=%d", len(orders), orders, invalid)
	if invalid != 0 {
		t.Fatalf("canonical finite configuration rejected by %d/128 actual constructors", invalid)
	}
}

func TestNumericReadinessSelectedCopyPreservesLegacy(t *testing.T) {
	for _, order := range [][]string{
		{"toxicity", "hate_speech", "violence", "sexual", "self_harm"},
		{"self_harm", "sexual", "violence", "hate_speech", "toxicity"},
	} {
		t.Run(order[0]+"-first", func(t *testing.T) {
			g := New(numericBoundaryConfig(t))
			byName := map[string]categoryChecker{}
			var phrases []string
			for _, cat := range g.categories {
				byName[cat.name] = cat
				phrases = append(phrases, cat.phrases...)
			}
			for i, name := range order {
				g.categories[i] = byName[name]
			}
			input := makeInput(strings.Join(phrases, "; "))
			before := g.Check(context.Background(), input)
			selected := g.SelectedPolicyGuardrail().(*ContentModerationGuardrail)
			if selected == g || &selected.categories[0] == &g.categories[0] {
				t.Fatal("selected copy shares category ordering")
			}
			if err := selected.ValidateSelectedThreshold(.8); err != nil {
				t.Fatal(err)
			}
			if result := selected.Check(context.Background(), input); result.Score != 1 || result.Pass || result.Err != nil {
				t.Fatalf("canonical evaluator=%+v", result)
			}
			after := g.Check(context.Background(), input)
			if !reflect.DeepEqual(categoryNames(g), order) || math.Float64bits(before.Score) != math.Float64bits(after.Score) || before.Message != after.Message {
				t.Fatal("selected readiness changed legacy order, numeric score or message")
			}
			// The small-first original deliberately retains even its legacy NaN;
			// selected readiness must not silently normalize org/static execution.
			if (order[0] != "toxicity") != math.IsNaN(after.Score) {
				t.Fatalf("legacy arithmetic changed: order=%v score=%g", order, after.Score)
			}
		})
	}
}

func TestNumericReadinessMalformedAndSnapshot(t *testing.T) {
	for _, tc := range []struct {
		name, field string
		value       interface{}
	}{
		{"selector-type", "categories", "toxicity"},
		{"selector-element", "categories", []interface{}{"toxicity", 42}},
		{"count-fraction", "category_threshold", 3.5},
		{"count-zero", "category_threshold", 0.0},
		{"count-string", "category_threshold", "3"},
		{"count-bool", "category_threshold", true},
		{"count-overflow", "category_threshold", math.Ldexp(1, 63)},
		{"weights-type", "weights", []interface{}{}},
		{"weight-string", "weights", map[string]interface{}{"toxicity": "2"}},
		{"weight-nan", "weights", map[string]interface{}{"toxicity": math.NaN()}},
		{"weight-inf", "weights", map[string]interface{}{"toxicity": math.Inf(1)}},
		{"true-overflow", "weights", map[string]interface{}{"toxicity": math.MaxFloat64, "sexual": math.MaxFloat64}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := numericBoundaryConfig(t)
			cfg[tc.field] = tc.value
			g := New(cfg)
			originalErr := g.ValidateConfig()
			if originalErr == nil {
				t.Fatal("malformed original configuration accepted")
			}
			cfg[tc.field] = numericBoundaryConfig(t)[tc.field]
			selected := g.SelectedPolicyGuardrail().(*ContentModerationGuardrail)
			if selected.ValidateConfig() != originalErr || selected.ValidateSelectedThreshold(0) != originalErr {
				t.Fatal("selected copy or caller correction cleared genuine original error")
			}
		})
	}
	cfg := numericBoundaryConfig(t)
	g := New(cfg)
	cfg["weights"].(map[string]interface{})["toxicity"] = math.Inf(1)
	cfg["categories"].([]interface{})[0] = "unknown"
	cfg["category_threshold"] = 3.5
	if err := g.SelectedPolicyGuardrail().(*ContentModerationGuardrail).ValidateSelectedThreshold(.8); err != nil {
		t.Fatalf("caller mutation changed valid constructor snapshot: %v", err)
	}
}

func TestNumericReadinessActualResolverPaths(t *testing.T) {
	// BindRealtime calls getKeyPolicyGuardrail directly (no HTTP admission
	// revalidation). Two binds prove fresh/cache paths; nil factory proves static.
	for _, path := range []string{"dynamic", "static-fallback"} {
		t.Run(path, func(t *testing.T) {
			for i := 0; i < 64; i++ {
				cfg := numericBoundaryConfig(t)
				g := New(cfg)
				originalOrder := categoryNames(g)
				registry := map[string]guardrails.Guardrail{"content-moderation": g}
				store := tenant.NewStore()
				store.Set("numeric-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{{
					ID: "numeric-policy", Mode: "enforce", KeyIDs: []string{"selected"},
					Checks: map[string]*tenant.GuardrailCheck{"content-moderation": {Enabled: true, Action: "block", ConfidenceThreshold: .8, Config: cfg}},
				}}}})
				factoryCalls := 0
				var factory guardrails.DynamicFactory
				if path == "dynamic" {
					factory = func(name string, config map[string]interface{}) guardrails.Guardrail {
						factoryCalls++
						return New(config)
					}
				}
				plugin := guardrails.NewPlugin(nil, registry, factory, nil, store)
				rc := models.AcquireRequestContext()
				rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "numeric-org", "selected"
				for attempt := 0; attempt < 2; attempt++ {
					bound, err := plugin.BindRealtime(rc, guardrails.RealtimeTextBoth)
					if err != nil {
						t.Errorf("constructor=%d attempt=%d bind=%v", i, attempt, err)
						continue
					}
					if !bound.Blocking(guardrails.StagePre) {
						t.Fatal("selected check missing")
					}
					if err := bound.Check(context.Background(), guardrails.StagePre, "The weather is pleasant today."); err != nil {
						t.Errorf("constructor=%d attempt=%d safe text unavailable: %v", i, attempt, err)
					}
					if err := bound.Check(context.Background(), guardrails.StagePre, "you are stupid; you're an idiot; shut up"); err == nil || strings.Contains(err.Error(), "unavailable") {
						t.Errorf("constructor=%d attempt=%d expected actual content verdict, got %v", i, attempt, err)
					}
				}
				rc.Metadata["auth_key_id"] = "unselected"
				bound, err := plugin.BindRealtime(rc, guardrails.RealtimeTextBoth)
				if err != nil || bound.Has(guardrails.StagePre) {
					t.Fatal("unselected key acquired a check")
				}
				rc.Release()
				wantCalls := 0
				if path == "dynamic" {
					wantCalls = 1
				}
				if factoryCalls != wantCalls || !reflect.DeepEqual(originalOrder, categoryNames(g)) {
					t.Fatalf("factory calls=%d want=%d or static ordering mutated", factoryCalls, wantCalls)
				}
			}
			t.Log(fmt.Sprintf("path=%s constructors=64 binds=128 (fresh/cache for dynamic), unselected=64", path))
		})
	}
}
