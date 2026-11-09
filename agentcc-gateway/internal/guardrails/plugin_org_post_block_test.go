package guardrails

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgPostPlugin is a plugin with no static rules and one org (tenant-config)
// check, named "output-check", whose guardrail runs on the output and fails
// every answer; action is the org's action for it.
func orgPostPlugin(action string) *GuardrailPlugin {
	registry := map[string]Guardrail{
		"output-check": &mockGuardrail{
			name:   "output-check",
			stage:  StagePost,
			result: &CheckResult{Pass: false, Score: 0.92, Message: "unsafe answer"},
		},
	}
	tenants := tenant.NewStore()
	tenants.Set("org-post", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
		Checks: map[string]*tenant.GuardrailCheck{
			"output-check": {Enabled: true, Action: action, ConfidenceThreshold: 0.7},
		},
	}})
	return NewPlugin(nil, registry, nil, nil, tenants)
}

func orgPostContext() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["org_id"] = "org-post"
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Response = &models.ChatCompletionResponse{ID: "answer"}
	return rc
}

// TestPlugin_PostBlock for the tenant path: an org's output check in block
// mode withholds the answer exactly as a static rule does.
func TestPlugin_OrgPostBlock(t *testing.T) {
	plugin := orgPostPlugin("block")
	rc := orgPostContext()
	defer rc.Release()

	result := plugin.ProcessResponse(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("org post-block should return error")
	}
	if rc.Response != nil {
		t.Error("response should be cleared on org post-block")
	}
	if !rc.Flags.GuardrailTriggered {
		t.Error("GuardrailTriggered should be set")
	}
	if got := rc.Metadata["guardrail_action"]; got != "blocked" {
		t.Errorf("guardrail_action = %q, want blocked", got)
	}
	if got := rc.Metadata["guardrail_name"]; got != "output-check" {
		t.Errorf("guardrail_name = %q, want output-check", got)
	}
}

// An org output check that only warns or logs keeps the answer.
func TestPlugin_OrgPostWarnAndLogKeepTheAnswer(t *testing.T) {
	for _, tc := range []struct{ action, want string }{{"warn", "warned"}, {"log", "logged"}} {
		t.Run(tc.action, func(t *testing.T) {
			plugin := orgPostPlugin(tc.action)
			rc := orgPostContext()
			defer rc.Release()

			result := plugin.ProcessResponse(context.Background(), rc)
			if result.Error != nil {
				t.Fatalf("a %s-mode org output check returned an error: %v", tc.action, result.Error)
			}
			if rc.Response == nil || rc.Response.ID != "answer" {
				t.Errorf("a %s-mode org output check dropped the answer", tc.action)
			}
			if got := rc.Metadata["guardrail_action"]; got != tc.want {
				t.Errorf("guardrail_action = %q, want %s", got, tc.want)
			}
		})
	}
}

// A post-stage block records its error on the request, static or org, so the
// handler answers it (403 content_blocked) and the request log records that
// status: the pipeline only logs a post-plugin's error.
func TestPlugin_PostBlockRecordsTheGuardrailError(t *testing.T) {
	static := NewPlugin(NewEngine(config.GuardrailsConfig{
		Enabled:        true,
		DefaultTimeout: 5 * time.Second,
		Rules:          []config.GuardrailRuleConfig{{Name: "output-check", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.7}},
	}, map[string]Guardrail{"output-check": &mockGuardrail{name: "output-check", stage: StagePost,
		result: &CheckResult{Pass: false, Score: 0.92, Message: "unsafe answer"}}}), nil, nil, nil, nil)
	for name, plugin := range map[string]*GuardrailPlugin{"static": static, "org": orgPostPlugin("block")} {
		t.Run(name, func(t *testing.T) {
			rc := orgPostContext()
			defer rc.Release()

			result := plugin.ProcessResponse(context.Background(), rc)
			if len(rc.Errors) != 1 {
				t.Fatalf("rc.Errors = %v, want the block's error", rc.Errors)
			}
			apiErr, ok := rc.Errors[0].(*models.APIError)
			if !ok || apiErr != result.Error || apiErr.Status != http.StatusForbidden || apiErr.Code != "content_blocked" {
				t.Errorf("recorded error = %#v, want the returned 403 content_blocked %#v", rc.Errors[0], result.Error)
			}
		})
	}
}

// A post-stage block with no answer to withhold (the provider failed) leaves
// the request's error as it is: the provider's error stays the one the log
// records.
func TestPlugin_PostBlockWithNoAnswerKeepsTheRequestError(t *testing.T) {
	plugin := orgPostPlugin("block")
	rc := orgPostContext()
	defer rc.Release()
	rc.Response = nil
	providerErr := models.ErrUpstreamProvider(http.StatusBadGateway, "upstream down")
	rc.AddError(providerErr)

	plugin.ProcessResponse(context.Background(), rc)
	if len(rc.Errors) != 1 || rc.Errors[0] != error(providerErr) {
		t.Errorf("rc.Errors = %v, want only the provider's error", rc.Errors)
	}
}
