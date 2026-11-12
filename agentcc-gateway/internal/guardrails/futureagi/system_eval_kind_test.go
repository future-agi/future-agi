package futureagi

import (
	"bytes"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// systemEvalsDir is the system evals' YAML in this repository
// (futureagi/model_hub/system_evals), as seed_system_evals reads it.
var systemEvalsDir = filepath.Join("..", "..", "..", "..", "futureagi", "model_hub", "system_evals")

var (
	yamlEvalID = regexp.MustCompile(`(?m)^eval_id:\s*(\d+)\s*$`)
	yamlName   = regexp.MustCompile(`(?m)^name:\s*(\S+)\s*$`)
)

// systemEvalKinds is generated from the system evals' YAML and
// shapeSystemEvalNames: every system eval's eval_id with its name, shape
// when its name is in the list, says otherwise; and every listed name is a
// system eval. A change to the seed or the list fails here until the table
// is regenerated.
func TestSystemEvalKindsMatchTheSeed(t *testing.T) {
	if _, err := os.Stat(systemEvalsDir); err != nil {
		t.Skipf("no system evals at %s (the gateway is built outside this repository): %v", systemEvalsDir, err)
	}
	want := map[string]systemEval{}
	names := map[string]bool{}
	for _, dir := range []string{"agent", "function", "specialty"} {
		files, err := filepath.Glob(filepath.Join(systemEvalsDir, dir, "*.yaml"))
		if err != nil {
			t.Fatal(err)
		}
		for _, f := range files {
			b, err := os.ReadFile(f)
			if err != nil {
				t.Fatal(err)
			}
			ids, ns := yamlEvalID.FindAllSubmatch(b, -1), yamlName.FindAllSubmatch(b, -1)
			if len(ids) != 1 || len(ns) != 1 {
				t.Fatalf("%s: want one top-level eval_id and name, found %d and %d", f, len(ids), len(ns))
			}
			id, name := string(ids[0][1]), string(ns[0][1])
			if prev, dup := want[id]; dup {
				t.Fatalf("eval_id %s is both %s and %s", id, prev.name, name)
			}
			kind := guardrails.JudgesSays
			if shapeSystemEvalNames[name] {
				kind = guardrails.JudgesShape
			}
			want[id] = systemEval{name, kind}
			names[name] = true
		}
	}
	if len(want) == 0 {
		t.Fatalf("no system evals under %s", systemEvalsDir)
	}
	for name := range shapeSystemEvalNames {
		if !names[name] {
			t.Errorf("shapeSystemEvalNames lists %q, which is no system eval", name)
		}
	}
	var diffs []string
	for id, w := range want {
		if got, ok := systemEvalKinds[id]; !ok || got != w {
			diffs = append(diffs, "eval_id "+id+": table has "+got.name+"/"+got.kind+", seed gives "+w.name+"/"+w.kind)
		}
	}
	for id, got := range systemEvalKinds {
		if _, ok := want[id]; !ok {
			diffs = append(diffs, "eval_id "+id+" ("+got.name+") is in the table but no system eval")
		}
	}
	sort.Strings(diffs)
	if len(diffs) > 0 {
		t.Errorf("systemEvalKinds has drifted from the seed; regenerate it:\n%s", strings.Join(diffs, "\n"))
	}
}

func TestDerivedJudges(t *testing.T) {
	for _, tc := range []struct {
		ids  []string
		want string
	}{
		{nil, ""},
		{[]string{"23"}, guardrails.JudgesShape},       // is_json
		{[]string{"23", "37"}, guardrails.JudgesShape}, // is_json, json_scheme_validation
		{[]string{"15"}, guardrails.JudgesSays},        // toxicity
		{[]string{"15", "18", "22", "69", "76"}, guardrails.JudgesSays},
		{[]string{"23", "15"}, ""},     // mixed
		{[]string{"15", "999999"}, ""}, // not a system eval's
		{[]string{"0"}, ""},
		{[]string{"toxicity"}, ""},
		{[]string{" 23 "}, guardrails.JudgesShape},
	} {
		if got := derivedJudges(tc.ids); got != tc.want {
			t.Errorf("derivedJudges(%q) = %q, want %q", tc.ids, got, tc.want)
		}
	}
}

// An evaluation's kind: its "judges", else its eval ids', else unknown; an
// invalid "judges" is a config error.
func TestEvaluationKind(t *testing.T) {
	base := func(judges interface{}, ids ...interface{}) map[string]interface{} {
		cfg := map[string]interface{}{"eval_ids": ids, "api_key": "k", "secret_key": "s"}
		if judges != nil {
			cfg["judges"] = judges
		}
		return cfg
	}
	for _, tc := range []struct {
		name                   string
		cfg                    map[string]interface{}
		shape, unknown, broken bool
		source                 string
	}{
		{"set says beside shape ids", base("says", "23"), false, false, false, guardrails.KindSet},
		{"set shape beside says ids", base("shape", "15"), true, false, false, guardrails.KindSet},
		{"derived shape", base(nil, "23"), true, false, false, guardrails.KindDerived},
		{"derived says", base(nil, "15"), false, false, false, guardrails.KindDerived},
		{"mixed", base(nil, "23", "15"), false, true, false, guardrails.KindNone},
		{"custom", base(nil, "999999"), false, true, false, guardrails.KindNone},
		{"invalid", base("content", "15"), false, true, true, guardrails.KindInvalid},
	} {
		g := NewConfigFileRule("eval", tc.cfg)
		if g.JudgesShape() != tc.shape || g.KindUnknown() != tc.unknown || g.KindSource() != tc.source || (g.configErr != nil) != tc.broken {
			t.Errorf("%s: shape %v unknown %v source %q configErr %v; want %v %v %q broken %v", tc.name,
				g.JudgesShape(), g.KindUnknown(), g.KindSource(), g.configErr, tc.shape, tc.unknown, tc.source, tc.broken)
		}
		if err := ValidateConfig("eval", tc.cfg); (err != nil) != tc.broken || (err != nil && !strings.Contains(err.Error(), `"judges" must be`)) {
			t.Errorf("%s: ValidateConfig = %v", tc.name, err)
		}
	}
}

// The engine logs the kind each rule runs with and how it was decided:
// names and kinds only, never a rule's config.
func TestEngineLogsEachRulesKind(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	defer slog.SetDefault(prev)

	const secret = "sk-not-for-logs-7f3a"
	cfg := func(judges interface{}, id string) map[string]interface{} {
		c := map[string]interface{}{"eval_ids": []interface{}{id}, "api_key": secret, "secret_key": secret}
		if judges != nil {
			c["judges"] = judges
		}
		return c
	}
	registry := map[string]guardrails.Guardrail{
		"eval-derived": NewConfigFileRule("eval-derived", cfg(nil, "23")),
		"eval-set":     NewConfigFileRule("eval-set", cfg("says", "23")),
		"eval-custom":  NewConfigFileRule("eval-custom", cfg(nil, "999999")),
	}
	var rules []config.GuardrailRuleConfig
	for _, name := range []string{"eval-derived", "eval-set", "eval-custom"} {
		rules = append(rules, config.GuardrailRuleConfig{Name: name, Stage: "post", Mode: "sync", Action: "block"})
	}
	guardrails.NewEngine(config.GuardrailsConfig{Enabled: true, Rules: rules}, registry)

	out := buf.String()
	for _, want := range []string{
		`msg="guardrail rule kind" name=eval-derived stage=post check_kind=shape kind_source=derived`,
		`msg="guardrail rule kind" name=eval-set stage=post check_kind=says kind_source=set`,
		`msg="guardrail rule kind" name=eval-custom stage=post check_kind=unknown kind_source=none`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("the log has no line with %s:\n%s", want, out)
		}
	}
	if strings.Contains(out, secret) {
		t.Errorf("the log holds a rule's credential:\n%s", out)
	}
}
