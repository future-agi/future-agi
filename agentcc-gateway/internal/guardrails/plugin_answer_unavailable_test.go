package guardrails

import (
	"context"
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// BlockingPost names the post-stage rules that would refuse an answer: the
// sync rules whose action is block after the key's policy and the request's
// X-Guardrail-Policy, as RunPost applies them.
func TestEngine_BlockingPost(t *testing.T) {
	answer := &mockGuardrail{name: "answer-check", stage: StagePost, result: &CheckResult{Pass: true}}
	registry := map[string]Guardrail{"answer-check": answer}
	engine := func(action, mode string) *Engine {
		return NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{
			{Name: "answer-check", Stage: "post", Mode: mode, Action: action, Threshold: 0.5}}}, registry)
	}
	warn := "warn"
	for _, tc := range []struct {
		name   string
		engine *Engine
		key    *policy.Policy
		req    policy.RequestPolicy
		want   []string
	}{
		{"a block rule", engine("block", "sync"), nil, policy.RequestPolicyNone, []string{"answer-check"}},
		{"a warn rule", engine("warn", "sync"), nil, policy.RequestPolicyNone, nil},
		{"an async block rule only logs", engine("block", "async"), nil, policy.RequestPolicyNone, nil},
		{"the key's policy makes it warn", engine("block", "sync"),
			&policy.Policy{Overrides: map[string]policy.Override{"answer-check": {Action: warn}}}, policy.RequestPolicyNone, nil},
		{"the key's policy disables it", engine("block", "sync"),
			&policy.Policy{Overrides: map[string]policy.Override{"answer-check": {Disabled: true}}}, policy.RequestPolicyNone, nil},
		{"X-Guardrail-Policy log-only", engine("block", "sync"), nil, policy.RequestPolicyLogOnly, nil},
		{"X-Guardrail-Policy disabled", engine("block", "sync"), nil, policy.RequestPolicyDisabled, nil},
		{"X-Guardrail-Policy strict blocks a warn rule", engine("warn", "sync"), nil, policy.RequestPolicyStrict, []string{"answer-check"}},
		{"no engine", nil, nil, policy.RequestPolicyNone, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.engine.BlockingPost(tc.key, tc.req); !slices.Equal(got, tc.want) {
				t.Errorf("BlockingPost = %v, want %v", got, tc.want)
			}
		})
	}
}

// unavailablePlugin is a plugin whose org has one check of each kind:
// "answer-check" reads the answer, "request-check" the request; action is
// the org's action for both.
func unavailablePlugin(action string) *GuardrailPlugin {
	registry := map[string]Guardrail{
		"answer-check":  &mockGuardrail{name: "answer-check", stage: StagePost, result: &CheckResult{Pass: true}},
		"request-check": &mockGuardrail{name: "request-check", stage: StagePre, result: &CheckResult{Pass: true}},
	}
	tenants := tenant.NewStore()
	tenants.Set("org-unavailable", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
		"answer-check":  {Enabled: true, Action: action},
		"request-check": {Enabled: true, Action: action},
	}}})
	return NewPlugin(nil, registry, nil, nil, tenants)
}

func unavailableContext(unavailable bool) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org-unavailable"
	if unavailable {
		rc.MarkAnswerUnavailable("the answer of a background response does not pass through the gateway")
	}
	return rc
}

// A request whose answer no check can read is refused before the provider
// call when a check on the answer would block it, and only then.
func TestPlugin_RefusesARequestWhoseAnswerNoCheckCanRead(t *testing.T) {
	t.Run("a blocking check on the answer", func(t *testing.T) {
		rc := unavailableContext(true)
		defer rc.Release()
		result := unavailablePlugin("block").ProcessRequest(context.Background(), rc)
		apiErr := result.Error
		if apiErr == nil || apiErr.Status != http.StatusForbidden || apiErr.Code != "answer_unavailable" {
			t.Fatalf("result error %v, want 403 answer_unavailable", result.Error)
		}
		for _, want := range []string{"answer-check", "checks the model's answer", "background response"} {
			if !strings.Contains(apiErr.Message, want) {
				t.Errorf("message %q does not say %q", apiErr.Message, want)
			}
		}
		if !rc.Flags.GuardrailTriggered || rc.Metadata["guardrail_action"] != "blocked" || rc.Metadata["guardrail_name"] != "answer-check" {
			t.Errorf("the block is not recorded: triggered %v, metadata %v", rc.Flags.GuardrailTriggered, rc.Metadata)
		}
		if len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].Action != "block" {
			t.Errorf("guardrail results %+v, want the check's block", rc.GuardrailResults)
		}
	})
	for _, tc := range []struct {
		name        string
		action      string
		unavailable bool
	}{
		{"checks in warn mode", "warn", true},
		{"the answer will be read", "block", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := unavailableContext(tc.unavailable)
			defer rc.Release()
			if result := unavailablePlugin(tc.action).ProcessRequest(context.Background(), rc); result.Error != nil {
				t.Errorf("refused: %v", result.Error)
			}
		})
	}
}

// An answer the gateway could not read is withheld at the post stage when a
// check on the answer would block it, and kept otherwise.
func TestPlugin_WithholdsAnAnswerNoCheckCanRead(t *testing.T) {
	for _, tc := range []struct {
		action   string
		withheld bool
	}{{"block", true}, {"warn", false}} {
		t.Run(tc.action, func(t *testing.T) {
			rc := unavailableContext(true)
			defer rc.Release()
			rc.Response = &models.ChatCompletionResponse{ID: "unreadable"}
			result := unavailablePlugin(tc.action).ProcessResponse(context.Background(), rc)
			if tc.withheld {
				apiErr := result.Error
				if apiErr == nil || apiErr.Code != "answer_unavailable" || rc.Response != nil || !rc.Flags.AnswerRefused {
					t.Errorf("error %v, response %v, refused %v; want the answer withheld with answer_unavailable",
						result.Error, rc.Response, rc.Flags.AnswerRefused)
				}
				return
			}
			if result.Error != nil || rc.Response == nil {
				t.Errorf("error %v, response %v; want the answer kept", result.Error, rc.Response)
			}
		})
	}
}
