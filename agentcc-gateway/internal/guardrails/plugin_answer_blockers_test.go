package guardrails

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Before the provider call, the plugin says which of the output checks that
// read the answer would block it (rc.Flags.AnswerBlockers), so a stream
// handler holds a stream such a check reads until the check has passed: a
// rule in the config file at the post stage whose action is block after the
// key's policy and X-Guardrail-Policy (a sync rule; an async one only logs),
// and an organization's enabled check that reads the answer in block mode.
func TestPlugin_MarksWhichChecksBlockTheAnswer(t *testing.T) {
	rule := func(action, mode string) *config.GuardrailsConfig {
		return &config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second,
			Rules: []config.GuardrailRuleConfig{{Name: "answer-check", Stage: "post", Mode: mode, Action: action, Threshold: 0.5}}}
	}
	override := map[string]string{"key_allow_policy_override": "true"}
	withHeader := func(v string) map[string]string {
		return map[string]string{"key_allow_policy_override": "true", "x-guardrail-policy": v}
	}
	for _, tc := range []struct {
		name    string
		rules   *config.GuardrailsConfig
		checks  map[string]*tenant.GuardrailCheck
		headers map[string]string
		want    models.AnswerReaders
	}{
		{name: "nothing configured"},
		{name: "a config rule on the answer in block mode", rules: rule("block", "sync"), want: models.AnswerReadByRules},
		{name: "a config rule on the answer in warn mode", rules: rule("warn", "sync")},
		{name: "a config rule on the answer in log mode", rules: rule("log", "sync")},
		{name: "an async config rule in block mode only logs", rules: rule("block", "async")},
		{name: "X-Guardrail-Policy log-only", rules: rule("block", "sync"), headers: withHeader("log-only")},
		{name: "X-Guardrail-Policy disabled", rules: rule("block", "sync"), headers: withHeader("disabled")},
		{name: "X-Guardrail-Policy strict makes a warn rule block", rules: rule("warn", "sync"), headers: withHeader("strict"),
			want: models.AnswerReadByRules},
		{name: "a key that may override, without the header", rules: rule("block", "sync"), headers: override,
			want: models.AnswerReadByRules},
		{name: "an org check that reads the answer in block mode",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: true, Action: "block"}},
			want:   models.AnswerReadByOrg},
		{name: "an org check that reads the answer in warn mode",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: true, Action: "warn"}}},
		{name: "an org check that reads the request in block mode",
			checks: map[string]*tenant.GuardrailCheck{"request-check": {Enabled: true, Action: "block"}}},
		{name: "a disabled org check in block mode",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: false, Action: "block"}}},
		{name: "one of two org checks in block mode",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: true, Action: "warn"},
				"other-answer-check": {Enabled: true, Action: "block"}},
			want: models.AnswerReadByOrg},
		{name: "both", rules: rule("block", "sync"),
			checks: map[string]*tenant.GuardrailCheck{"other-answer-check": {Enabled: true, Action: "block"}},
			want:   models.AnswerReadByRules | models.AnswerReadByOrg},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reg := map[string]Guardrail{
				"answer-check":       &mockGuardrail{name: "answer-check", stage: StagePost, result: &CheckResult{Pass: true}},
				"other-answer-check": &mockGuardrail{name: "other-answer-check", stage: StagePost, result: &CheckResult{Pass: true}},
				"request-check":      &mockGuardrail{name: "request-check", stage: StagePre, result: &CheckResult{Pass: true}},
			}
			var engine *Engine
			if tc.rules != nil {
				engine = NewEngine(*tc.rules, reg)
			}
			store := tenant.NewStore()
			store.Set("org-blockers", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: tc.checks}})
			plugin := NewPlugin(engine, reg, nil, nil, store)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org-blockers"
			for k, v := range tc.headers {
				rc.Metadata[k] = v
			}
			rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
			if result := plugin.ProcessRequest(context.Background(), rc); result.Error != nil {
				t.Fatalf("ProcessRequest: %v", result.Error)
			}
			if rc.Flags.AnswerBlockers != tc.want {
				t.Errorf("AnswerBlockers = %b, want %b", rc.Flags.AnswerBlockers, tc.want)
			}
			if rc.Flags.AnswerBlockers&^rc.Flags.AnswerReaders != 0 {
				t.Errorf("AnswerBlockers %b names a check AnswerReaders %b does not", rc.Flags.AnswerBlockers, rc.Flags.AnswerReaders)
			}
		})
	}
}

// The streaming checker blocks (so a stream handler holds its stream) when
// its failure action is stop and a rule it runs blocks under its key's policy
// and the request's policy; in disclaimer mode it never blocks.
func TestStreamChecker_Blocks(t *testing.T) {
	newChecker := func(action, failure string, keyPolicy *policy.Policy, rp policy.RequestPolicy) *StreamGuardrailChecker {
		reg := map[string]Guardrail{"answer-check": &mockGuardrail{name: "answer-check", stage: StagePost, result: &CheckResult{Pass: true}}}
		engine := NewEngine(config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second,
			Rules: []config.GuardrailRuleConfig{{Name: "answer-check", Stage: "post", Mode: "sync", Action: action, Threshold: 0.5}}}, reg)
		return NewStreamChecker(engine, config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 10, FailureAction: failure},
			keyPolicy, rp, &models.ChatCompletionRequest{Model: "gpt-4o"}, nil)
	}
	logOnly := &policy.Policy{Overrides: map[string]policy.Override{"answer-check": {Action: "log"}}}
	for _, tc := range []struct {
		name string
		sc   *StreamGuardrailChecker
		want bool
	}{
		{"no checker", nil, false},
		{"stop, a block rule", newChecker("block", "stop", nil, policy.RequestPolicyNone), true},
		{"default failure action, a block rule", newChecker("block", "", nil, policy.RequestPolicyNone), true},
		{"disclaimer, a block rule", newChecker("block", "disclaimer", nil, policy.RequestPolicyNone), false},
		{"stop, a warn rule", newChecker("warn", "stop", nil, policy.RequestPolicyNone), false},
		{"stop, a block rule the key's policy logs", newChecker("block", "stop", logOnly, policy.RequestPolicyNone), false},
		{"stop, a block rule, X-Guardrail-Policy disabled", newChecker("block", "stop", nil, policy.RequestPolicyDisabled), false},
		{"stop, a warn rule, X-Guardrail-Policy strict", newChecker("warn", "stop", nil, policy.RequestPolicyStrict), true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.sc.Blocks(); got != tc.want {
				t.Errorf("Blocks() = %t, want %t", got, tc.want)
			}
		})
	}
}
