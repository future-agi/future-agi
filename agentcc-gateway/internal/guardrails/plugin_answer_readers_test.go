package guardrails

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Before the provider call, the plugin says which output checks will read the
// answer (rc.Flags.AnswerReaders), so a handler that would otherwise give
// the post stage only the answer's usage gives it the answer itself: the
// config file's post-stage rules, and an organization's enabled checks whose
// type reads the answer. A check whose type reads the request, a disabled
// check and a check a config rule shadows do not count.
func TestPlugin_MarksWhichChecksReadTheAnswer(t *testing.T) {
	registry := map[string]Guardrail{
		"answer-check":  &mockGuardrail{name: "answer-check", stage: StagePost, result: &CheckResult{Pass: true}},
		"request-check": &mockGuardrail{name: "request-check", stage: StagePre, result: &CheckResult{Pass: true}},
	}
	rule := func(name, stage string) config.GuardrailsConfig {
		return config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second,
			Rules: []config.GuardrailRuleConfig{{Name: name, Stage: stage, Mode: "sync", Action: "block", Threshold: 0.5}}}
	}
	org := func(checks map[string]*tenant.GuardrailCheck) *tenant.Store {
		store := tenant.NewStore()
		store.Set("org-readers", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
		return store
	}
	for _, tc := range []struct {
		name    string
		rules   *config.GuardrailsConfig
		checks  map[string]*tenant.GuardrailCheck
		headers map[string]string
		want    models.AnswerReaders
	}{
		{name: "nothing configured"},
		{name: "a config rule on the answer", rules: ptr(rule("answer-check", "post")), want: models.AnswerReadByRules},
		{name: "a config rule on the request", rules: ptr(rule("request-check", "pre"))},
		{name: "X-Guardrail-Policy disabled turns the rules off", rules: ptr(rule("answer-check", "post")),
			headers: map[string]string{"x-guardrail-policy": "disabled", "key_allow_policy_override": "true"}},
		{name: "an org check whose type reads the answer",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: true, Action: "warn"}},
			want:   models.AnswerReadByOrg},
		{name: "an org check whose type reads the request",
			checks: map[string]*tenant.GuardrailCheck{"request-check": {Enabled: true, Action: "block"}}},
		{name: "a disabled org check",
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: false, Action: "block"}}},
		{name: "an org check a config rule runs instead", rules: ptr(rule("answer-check", "post")),
			checks: map[string]*tenant.GuardrailCheck{"answer-check": {Enabled: true, Action: "block"}},
			want:   models.AnswerReadByRules},
		{name: "both", rules: ptr(rule("answer-check", "post")),
			checks: map[string]*tenant.GuardrailCheck{"other-answer-check": {Enabled: true, Action: "block"}},
			want:   models.AnswerReadByRules | models.AnswerReadByOrg},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reg := map[string]Guardrail{}
			for k, v := range registry {
				reg[k] = v
			}
			reg["other-answer-check"] = &mockGuardrail{name: "other-answer-check", stage: StagePost, result: &CheckResult{Pass: true}}
			var engine *Engine
			if tc.rules != nil {
				engine = NewEngine(*tc.rules, reg)
			}
			plugin := NewPlugin(engine, reg, nil, nil, org(tc.checks))
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org-readers"
			for k, v := range tc.headers {
				rc.Metadata[k] = v
			}
			rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
			if result := plugin.ProcessRequest(context.Background(), rc); result.Error != nil {
				t.Fatalf("ProcessRequest: %v", result.Error)
			}
			if rc.Flags.AnswerReaders != tc.want {
				t.Errorf("AnswerReaders = %b, want %b", rc.Flags.AnswerReaders, tc.want)
			}
		})
	}
}

func ptr[T any](v T) *T { return &v }
