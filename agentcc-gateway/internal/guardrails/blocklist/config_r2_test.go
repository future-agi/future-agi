package blocklist

import (
	"context"
	"encoding/json"
	"math"
	"reflect"
	"strconv"
	"testing"
)

// Keep the regression executable on the pre-fix revision: a missing optional
// interface is a test failure, not a compile failure.
func configR2Validator(t *testing.T, g *BlocklistGuardrail) interface{ ValidateConfig() error } {
	t.Helper()
	v, ok := interface{}(g).(interface{ ValidateConfig() error })
	if !ok {
		t.Fatal("blocklist does not expose selected-policy configuration validation")
	}
	return v
}

func TestConfigR2RejectMalformedSelectedConfig(t *testing.T) {
	const wordsError = "keyword-blocklist words must be a nonempty array of nonblank strings"
	const wholeWordError = "keyword-blocklist whole_word must be a boolean"
	const maxMatchesError = "keyword-blocklist max_matches must be a positive integer within the supported range"
	cases := []struct {
		name string
		cfg  map[string]interface{}
		want string
	}{
		{"nil_config", nil, wordsError},
		{"missing_words", map[string]interface{}{}, wordsError},
	}
	for _, tc := range []struct {
		name  string
		value interface{}
	}{
		{"null", nil},
		{"string", "synthetic-sensitive-keyword"},
		{"number", float64(42)},
		{"boolean", true},
		{"object", map[string]interface{}{"private": "synthetic-sensitive-keyword"}},
		{"empty_array", []interface{}{}},
		{"typed_nil_array", []interface{}(nil)},
		{"non_json_string_slice", []string{"restricted"}},
		{"empty_entry", []interface{}{""}},
		{"blank_entry", []interface{}{" \t\r\n"}},
		{"unicode_blank_entry", []interface{}{"  "}},
		{"null_entry", []interface{}{nil}},
		{"number_entry", []interface{}{float64(42)}},
		{"boolean_entry", []interface{}{true}},
		{"object_entry", []interface{}{map[string]interface{}{"private": "synthetic-sensitive-keyword"}}},
		{"array_entry", []interface{}{[]interface{}{"restricted"}}},
		{"mixed_empty", []interface{}{"restricted", ""}},
		{"mixed_blank", []interface{}{"restricted", " \t"}},
		{"mixed_null", []interface{}{"restricted", nil}},
		{"mixed_number", []interface{}{"restricted", float64(42)}},
		{"mixed_boolean", []interface{}{"restricted", false}},
		{"mixed_object", []interface{}{"restricted", map[string]interface{}{}}},
		{"mixed_array", []interface{}{"restricted", []interface{}{"another"}}},
	} {
		cases = append(cases, struct {
			name string
			cfg  map[string]interface{}
			want string
		}{"words_" + tc.name, map[string]interface{}{"words": tc.value}, wordsError})
	}
	for _, tc := range []struct {
		name  string
		value interface{}
	}{
		{"null", nil}, {"string", "synthetic-sensitive-value"},
		{"number", float64(1)}, {"array", []interface{}{}}, {"object", map[string]interface{}{}},
	} {
		cases = append(cases, struct {
			name string
			cfg  map[string]interface{}
			want string
		}{"whole_word_" + tc.name, map[string]interface{}{"words": []interface{}{"restricted"}, "whole_word": tc.value}, wholeWordError})
	}
	for _, tc := range []struct {
		name  string
		value interface{}
	}{
		{"null", nil}, {"string", "synthetic-sensitive-value"}, {"boolean", true},
		{"array", []interface{}{}}, {"object", map[string]interface{}{}},
		{"zero_int", 0}, {"negative_int", -1}, {"zero_float", float64(0)},
		{"negative_float", float64(-1)}, {"fraction", 1.5}, {"small_fraction", 0.5},
		{"nan", math.NaN()}, {"infinity", math.Inf(1)}, {"negative_infinity", math.Inf(-1)},
		{"int_overflow", math.Ldexp(1, strconv.IntSize-1)}, {"huge_float", math.MaxFloat64},
		{"non_json_int64", int64(2)}, {"non_json_number", json.Number("2")},
	} {
		cases = append(cases, struct {
			name string
			cfg  map[string]interface{}
			want string
		}{"max_matches_" + tc.name, map[string]interface{}{"words": []interface{}{"restricted"}, "max_matches": tc.value}, maxMatchesError})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := configR2Validator(t, New(tc.cfg)).ValidateConfig()
			if err == nil || err.Error() != tc.want {
				t.Fatalf("expected sanitized configuration error %q, got %v", tc.want, err)
			}
		})
	}
}

func TestConfigR2ValidMatchingSemantics(t *testing.T) {
	cases := []struct {
		name  string
		json  string
		text  string
		pass  bool
		score float64
	}{
		{"exact_case_insensitive", `{"words":["RESTRICTED"]}`, "restricted", false, 1},
		{"clean", `{"words":["restricted"]}`, "ordinary text", true, 0},
		{"phrase", `{"words":["private phrase"]}`, "a PRIVATE PHRASE here", false, 1},
		{"wildcard", `{"words":["private*phrase"]}`, "private middle phrase", false, 1},
		{"wildcard_empty", `{"words":["private*phrase"]}`, "privatephrase", false, 1},
		{"wildcard_only", `{"words":["*"]}`, "anything", false, 1},
		{"literal_metacharacters", `{"words":["[secret].+(x)?^$\\"]}`, "[SECRET].+(x)?^$\\", false, 1},
		{"not_raw_regex", `{"words":["[secret].+"]}`, "secret text", true, 0},
		{"literal_unbalanced_bracket", `{"words":["["]}`, "[", false, 1},
		{"meaningful_whitespace_match", `{"words":[" restricted "]}`, " restricted ", false, 1},
		{"meaningful_whitespace_preserved", `{"words":[" restricted "]}`, "restricted", true, 0},
		{"whole_word_match", `{"words":["bad"],"whole_word":true}`, "this is BAD", false, 1},
		{"whole_word_substring", `{"words":["bad"],"whole_word":true}`, "badge", true, 0},
		{"substring", `{"words":["bad"],"whole_word":false}`, "badge", false, 1},
		{"whole_word_wildcard", `{"words":["bad*word"],"whole_word":true}`, "bad-middle-word", false, 1},
		{"partial_score", `{"words":["alpha","beta","gamma"],"max_matches":4}`, "alpha beta", false, 0.5},
		{"float_integer_score", `{"words":["alpha"],"max_matches":2.0}`, "alpha", false, 0.5},
		{"capped_score", `{"words":["alpha","beta"],"max_matches":1}`, "alpha beta", false, 1},
		{"unknown_config_preserved", `{"words":["restricted"],"custom_option":"unchanged"}`, "restricted", false, 1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var cfg map[string]interface{}
			if err := json.Unmarshal([]byte(tc.json), &cfg); err != nil {
				t.Fatal(err)
			}
			before, _ := json.Marshal(cfg)
			g := New(cfg)
			if err := configR2Validator(t, g).ValidateConfig(); err != nil {
				t.Fatalf("valid configuration rejected: %v", err)
			}
			after, _ := json.Marshal(cfg)
			if string(before) != string(after) {
				t.Fatal("validation mutated configuration")
			}
			r := g.Check(context.Background(), makeInput(tc.text))
			if r.Err != nil || r.Pass != tc.pass || r.Score != tc.score {
				t.Fatalf("unexpected match/scoring result: %+v", r)
			}
			if !tc.pass && len(cfg["words"].([]interface{})) == 1 {
				want := []string{cfg["words"].([]interface{})[0].(string)}
				if !reflect.DeepEqual(r.Details["matched_words"], want) {
					t.Fatal("matched word spelling or whitespace was changed")
				}
			}
		})
	}
	for _, n := range []interface{}{1, 2, int(^uint(0) >> 1), float64(2), math.Nextafter(math.Ldexp(1, strconv.IntSize-1), 0)} {
		g := New(map[string]interface{}{"words": []interface{}{"restricted"}, "max_matches": n})
		if err := configR2Validator(t, g).ValidateConfig(); err != nil {
			t.Fatalf("supported positive integer rejected: %v", err)
		}
	}
}

func TestConfigR2LegacyCheckCompatibility(t *testing.T) {
	// The leaf has no selection/enabled state. Org/static and unselected callers
	// continue using New/Check directly; admission opts in to ValidateConfig.
	cases := []struct {
		name string
		cfg  map[string]interface{}
		pass bool
	}{
		{"nil_config", nil, true},
		{"missing_words", map[string]interface{}{}, true},
		{"string_words", map[string]interface{}{"words": "restricted"}, true},
		{"empty_words", map[string]interface{}{"words": []interface{}{}}, true},
		{"mixed_words", map[string]interface{}{"words": []interface{}{"restricted", 42}}, false},
		{"invalid_whole_word", map[string]interface{}{"words": []interface{}{"restricted"}, "whole_word": "true"}, false},
		{"defaulted_max_matches", map[string]interface{}{"words": []interface{}{"restricted"}, "max_matches": 0}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := New(tc.cfg)
			before := g.Check(context.Background(), makeInput("restricted"))
			if before.Err != nil || before.Pass != tc.pass {
				t.Fatalf("legacy behavior changed: %+v", before)
			}
			if configR2Validator(t, g).ValidateConfig() == nil {
				t.Fatal("invalid config must be rejected when selected consumer opts in")
			}
			after := g.Check(context.Background(), makeInput("restricted"))
			if !reflect.DeepEqual(before, after) {
				t.Fatal("validation changed legacy Check behavior")
			}
		})
	}
}

func TestConfigR2ValidationUsesConstructionConfig(t *testing.T) {
	cfg := map[string]interface{}{"words": []interface{}{"restricted"}}
	valid := New(cfg)
	cfg["words"] = "malformed"
	invalid := New(cfg)
	cfg["words"] = []interface{}{"corrected"}
	if err := configR2Validator(t, valid).ValidateConfig(); err != nil {
		t.Fatal("later map mutation invalidated an already constructed checker")
	}
	if err := configR2Validator(t, invalid).ValidateConfig(); err == nil {
		t.Fatal("later map correction must not validate a checker constructed without patterns")
	}
}
