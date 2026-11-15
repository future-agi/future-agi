package contentmod

import (
	"context"
	"encoding/json"
	"math"
	"strconv"
	"testing"
)

func TestConfig1602ModerationCountReadiness(t *testing.T) {
	upper := math.Ldexp(1, strconv.IntSize-1)
	cases := []struct {
		name    string
		value   interface{}
		invalid bool
	}{
		{"int", 3, false}, {"one", float64(1), false}, {"integer-float", float64(3), false},
		{"large", float64(1000000), false}, {"max-int", int(^uint(0) >> 1), false},
		{"largest-in-range-float", math.Nextafter(upper, 0), strconv.IntSize == 32},
		{"zero", 0, true}, {"negative", -1, true}, {"zero-float", float64(0), true},
		{"fraction", 50.5, true}, {"subunit", 0.5, true}, {"negative-float", -1.0, true},
		{"nan", math.NaN(), true}, {"infinity", math.Inf(1), true}, {"negative-infinity", math.Inf(-1), true},
		{"exclusive-upper", upper, true}, {"max-float", math.MaxFloat64, true},
		{"null", nil, true}, {"string", "3", true}, {"boolean", true, true},
		{"json-number", json.Number("3"), true}, {"int64-not-consumed", int64(3), true},
		{"uint-not-consumed", uint(3), true}, {"float32-not-consumed", float32(3), true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(map[string]interface{}{"category_threshold": tc.value})
			if (g.ValidateConfig() != nil) != tc.invalid {
				t.Fatalf("readiness error=%v invalid=%v", g.ValidateConfig(), tc.invalid)
			}
			if !tc.invalid && g.ValidateSelectedThreshold(0) != nil {
				t.Fatal("reachable positive count rejected at outer threshold zero")
			}
		})
	}
	g := New(nil)
	if g.categoryThresh != 3 || g.ValidateConfig() != nil || g.ValidateSelectedThreshold(0.8) != nil {
		t.Fatal("default count/readiness changed")
	}
}

func TestConfig1602ModerationSelectors(t *testing.T) {
	cases := []struct {
		name    string
		cfg     map[string]interface{}
		invalid bool
		count   int
	}{
		{"defaults", nil, false, 5},
		{"empty", map[string]interface{}{"categories": []interface{}{}}, false, 5},
		{"valid", map[string]interface{}{"categories": []interface{}{"toxicity", "sexual"}}, false, 2},
		{"duplicate", map[string]interface{}{"categories": []interface{}{"toxicity", "toxicity"}}, false, 1},
		{"extra", map[string]interface{}{"future_option": []interface{}{42}}, false, 5},
		{"null", map[string]interface{}{"categories": nil}, true, 0},
		{"string", map[string]interface{}{"categories": "toxicity"}, true, 0},
		{"nonstring", map[string]interface{}{"categories": []interface{}{42}}, true, 0},
		{"unknown", map[string]interface{}{"categories": []interface{}{"not-a-category"}}, true, 0},
		{"mixed", map[string]interface{}{"categories": []interface{}{"toxicity", 42}}, true, 0},
		{"case-sensitive", map[string]interface{}{"categories": []interface{}{"Toxicity"}}, true, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(tc.cfg)
			if (g.ValidateConfig() != nil) != tc.invalid {
				t.Fatalf("readiness error=%v invalid=%v", g.ValidateConfig(), tc.invalid)
			}
			if !tc.invalid && len(g.categories) != tc.count {
				t.Fatalf("categories=%d want=%d", len(g.categories), tc.count)
			}
			result := g.Check(context.Background(), makeInput("The weather is pleasant today."))
			if result.Err != nil || !result.Pass || result.Score != 0 {
				t.Fatalf("benign legacy Check changed: %+v", result)
			}
		})
	}
}

func TestConfig1602ModerationReachability(t *testing.T) {
	for name, count := range map[string]int{"toxicity": 15, "hate_speech": 15, "violence": 15, "sexual": 13, "self_harm": 15} {
		phrases := defaultCategories()[name]
		if len(phrases) != count {
			t.Fatalf("catalog count changed for %s: %d", name, len(phrases))
		}
		seen := map[string]bool{}
		for _, phrase := range phrases {
			if phrase == "" || seen[phrase] {
				t.Fatalf("catalog contains an empty or duplicate phrase: %s", name)
			}
			seen[phrase] = true
		}
	}
	cases := []struct {
		name      string
		cfg       map[string]interface{}
		threshold float64
		invalid   bool
	}{
		{"defaults", nil, .8, false}, {"zero-is-not-default", nil, 0, false},
		{"one-strict", nil, 1, true},
		{"count50-unreachable", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 50}, .8, true},
		{"count50-reachable", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 50}, .29, false},
		{"count50-equality", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 50}, .3, true},
		{"count50-below-boundary", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 50}, math.Nextafter(.3, 0), false},
		{"large-reachable", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 1000000}, .00001, false},
		{"sexual-catalog13", map[string]interface{}{"categories": []interface{}{"sexual"}, "category_threshold": 50}, .26, true},
		{"sexual-below-boundary", map[string]interface{}{"categories": []interface{}{"sexual"}, "category_threshold": 50}, math.Nextafter(.26, 0), false},
		{"dominance-boost", map[string]interface{}{"category_threshold": 16}, .93, false},
		{"dominance-equality", map[string]interface{}{"category_threshold": 16}, .9375, true},
		{"weighted-low", map[string]interface{}{"categories": []interface{}{"toxicity", "sexual"}, "category_threshold": 17, "weights": map[string]interface{}{"sexual": 3.0}}, .8, true},
		{"weighted-high", map[string]interface{}{"categories": []interface{}{"toxicity", "sexual"}, "category_threshold": 17, "weights": map[string]interface{}{"toxicity": 3.0}}, .8, false},
		{"no-boost-below-point9", map[string]interface{}{"categories": []interface{}{"toxicity", "sexual"}, "category_threshold": 17, "weights": map[string]interface{}{"sexual": 100.0}}, .8, true},
		{"bad-outer-nan", nil, math.NaN(), true}, {"bad-outer-inf", nil, math.Inf(1), true},
		{"bad-outer-negative", nil, -.1, true}, {"bad-outer-large", nil, 2, true},
		{"malformed-original", map[string]interface{}{"category_threshold": 50.5}, 0, true},
		{"underflow-unreachable", map[string]interface{}{"categories": []interface{}{"toxicity"}, "category_threshold": 1000, "weights": map[string]interface{}{"toxicity": math.SmallestNonzeroFloat64}}, 0, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(tc.cfg)
			if err := g.ValidateSelectedThreshold(tc.threshold); (err != nil) != tc.invalid {
				t.Fatalf("threshold readiness error=%v invalid=%v", err, tc.invalid)
			}
		})
	}
}

func TestConfig1602ModerationWeightSemantics(t *testing.T) {
	cases := []struct {
		name    string
		value   interface{}
		invalid bool
		weight  float64
	}{
		{"positive", 2.0, false, 2}, {"fraction", .5, false, .5},
		{"zero-default", 0.0, false, 1}, {"negative-default", -2.0, false, 1},
		{"largest-single", math.MaxFloat64, false, math.MaxFloat64},
		{"nan", math.NaN(), true, 0}, {"infinity", math.Inf(1), true, 0},
		{"negative-infinity", math.Inf(-1), true, 0}, {"null", nil, true, 0},
		{"string", "2", true, 0}, {"boolean", true, true, 0},
		// Native int is ignored by the historical constructor; JSON numeric
		// weights arrive as float64, including integer-shaped JSON numbers.
		{"native-int-not-consumed", 2, true, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(map[string]interface{}{"categories": []interface{}{"toxicity"}, "weights": map[string]interface{}{"toxicity": tc.value}})
			if (g.ValidateConfig() != nil) != tc.invalid {
				t.Fatalf("readiness error=%v invalid=%v", g.ValidateConfig(), tc.invalid)
			}
			if !tc.invalid && (g.categories[0].weight != tc.weight || g.ValidateSelectedThreshold(.8) != nil) {
				t.Fatalf("constructor weight semantics changed: %v", g.categories[0].weight)
			}
		})
	}
	for _, value := range []interface{}{nil, "weights", []interface{}{}, map[string]float64{"toxicity": 2}} {
		if New(map[string]interface{}{"weights": value}).ValidateConfig() == nil {
			t.Fatal("malformed weight container accepted")
		}
	}
	if New(map[string]interface{}{"weights": map[string]interface{}{"future": "ignored"}}).ValidateConfig() != nil {
		t.Fatal("unknown weight key contract changed")
	}
	overflow := map[string]interface{}{"toxicity": math.MaxFloat64, "sexual": math.MaxFloat64}
	if New(map[string]interface{}{"weights": overflow}).ValidateConfig() == nil {
		t.Fatal("nonfinite effective weight sum accepted")
	}
	if New(map[string]interface{}{"categories": []interface{}{"toxicity"}, "weights": overflow}).ValidateConfig() != nil {
		t.Fatal("inactive weights incorrectly included in effective sum")
	}
}

func TestConfig1602ModerationJSONNumericParity(t *testing.T) {
	var cfg map[string]interface{}
	if err := json.Unmarshal([]byte(`{"category_threshold":17,"weights":{"toxicity":0.1,"hate_speech":0.2,"violence":0.3,"sexual":0.4,"self_harm":0.5}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	// Independent Python/JS arithmetic produces this exact binary64 maximum.
	// Fixed catalog order is required to agree at the strict equality boundary.
	const maximum = 0x1.b3b3b3b3b3b3bp-1
	order := []string{"toxicity", "hate_speech", "violence", "sexual", "self_harm"}
	for i := 0; i < 50; i++ {
		g := New(cfg)
		if g.ValidateConfig() != nil || len(g.categories) != len(defaultCategories()) {
			t.Fatal("JSON numeric configuration or catalog lost")
		}
		for j, cat := range g.categories {
			if cat.name != order[j] {
				t.Fatal("numeric accumulation order diverged from control plane")
			}
		}
		if g.ValidateSelectedThreshold(maximum) == nil || g.ValidateSelectedThreshold(math.Nextafter(maximum, 0)) != nil {
			t.Fatal("strict boundary diverged from Python/JS fixture")
		}
	}
	if err := json.Unmarshal([]byte(`{"category_threshold":50,"categories":["toxicity"],"weights":{"toxicity":2}}`), &cfg); err != nil {
		t.Fatal(err)
	}
	g := New(cfg)
	if g.ValidateConfig() != nil || g.ValidateSelectedThreshold(.29) != nil || g.categories[0].weight != 2 {
		t.Fatal("integer-shaped JSON weight must decode as a supported float64")
	}
}

func TestConfig1602ModerationOriginalInputSnapshot(t *testing.T) {
	cfg := map[string]interface{}{"category_threshold": 3.5}
	g := New(cfg)
	cfg["category_threshold"] = 3
	if g.ValidateConfig() == nil || g.ValidateSelectedThreshold(0) == nil {
		t.Fatal("original invalid count silently normalized")
	}
	valid := New(cfg)
	cfg["category_threshold"] = 3.5
	if valid.ValidateConfig() != nil || valid.ValidateSelectedThreshold(.8) != nil {
		t.Fatal("caller mutation changed constructor readiness")
	}
}
