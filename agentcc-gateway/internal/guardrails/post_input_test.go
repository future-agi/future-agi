package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type stageInputCheck struct {
	name  string
	stage Stage
	input *CheckInput
}

func (g *stageInputCheck) Name() string { return g.name }
func (g *stageInputCheck) Stage() Stage { return g.stage }
func (g *stageInputCheck) Check(_ context.Context, input *CheckInput) *CheckResult {
	g.input = input
	return &CheckResult{Pass: true}
}

func TestTenantPostInputPreservesEvaluatorContext(t *testing.T) {
	for _, name := range []string{"input-validation", "keyword-blocklist", "pii-detection", "content-moderation", "secret-detection", "inspect"} {
		for _, stage := range []Stage{StagePre, StagePost} {
			t.Run(fmt.Sprintf("%s/native_stage=%d", name, stage), func(t *testing.T) {
				rc := reflectionRC()
				defer rc.Release()
				rc.Metadata["org_id"] = "org"
				rc.Request.Stop = json.RawMessage(`"prompt stop"`)
				rc.Request.User = "prompt user"
				rc.Request.LogitBias = map[string]int{"123": 1}
				rc.Request.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "prompt tool"}}}
				rc.Request.ToolChoice = json.RawMessage(`{"type":"function","function":{"name":"prompt tool"}}`)
				rc.Request.ResponseFormat = &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"description":"prompt schema"}`)}
				rc.Request.Extra = map[string]json.RawMessage{"prompt": json.RawMessage(`"legacy prompt"`), "instructions": json.RawMessage(`"provider instructions"`)}
				rc.Response = reflectionResponse("candidate")
				before, _ := json.Marshal(rc.Request)
				check := &stageInputCheck{name: name, stage: stage}
				store := tenant.NewStore()
				store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{name: {Enabled: true, Action: "block", Config: map[string]interface{}{"stage": "post"}}}}})
				plugin := NewPlugin(nil, map[string]Guardrail{name: check}, nil, nil, store)
				if result := plugin.ProcessResponse(context.Background(), rc); result.Error != nil || check.input == nil || check.input.Request == nil || check.input.Response == nil {
					t.Fatalf("invalid stage input: result=%+v input=%+v", result, check.input)
				}
				request := check.input.Request
				if name != "inspect" {
					if len(request.Messages) != 0 || request.Stop != nil || request.User != "" || request.LogitBias != nil || request.Tools != nil || request.ToolChoice != nil || request.ResponseFormat != nil || request.Extra != nil || request.Model != rc.Model {
						t.Fatalf("prompt-bearing fields retained: %+v", request)
					}
				} else {
					got, _ := json.Marshal(request)
					if string(got) != string(before) {
						t.Fatal("general-purpose evaluator lost request context")
					}
				}
				after, _ := json.Marshal(rc.Request)
				if string(after) != string(before) {
					t.Fatal("response-stage input selection mutated the request")
				}
			})
		}
	}
}

func TestPostRejectionPreservesPreStageDiagnosticsAndFeedback(t *testing.T) {
	for _, hadWarnings := range []bool{false, true} {
		rc := reflectionRC()
		rc.Metadata["org_id"] = "org"
		if hadWarnings {
			rc.Metadata["guardrail_warnings"] = "safe pre-stage warning"
		}
		rc.GuardrailResults = []models.GuardrailResult{{Name: "pre", Message: "safe pre-stage result"}}
		rc.Response = reflectionResponse("bad candidate")
		registry := map[string]Guardrail{"block": &responseWordCheck{name: "block", word: "bad", stage: StagePost}, "warn": &responseWordCheck{name: "warn", word: "bad", stage: StagePost}}
		engine := NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "block", Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect"}}}}, registry)
		store := tenant.NewStore()
		store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"warn": {Enabled: true, Action: "warn"}}}})
		result := NewPlugin(engine, registry, nil, nil, store).ProcessResponse(context.Background(), rc)
		if result.Error == nil || result.Reflection == nil || !strings.Contains(result.Reflection.Reason, "remove bad") || rc.Response != nil {
			t.Fatalf("private feedback lost: %+v", result)
		}
		if rc.Metadata["guardrail_name"] != "block" || rc.Metadata["guardrail_action"] != "blocked" {
			t.Fatalf("blocking rule identity changed: %v", rc.Metadata)
		}
		warnings, present := rc.Metadata["guardrail_warnings"]
		if present != hadWarnings || (hadWarnings && warnings != "safe pre-stage warning") || len(rc.GuardrailResults) != 3 || rc.GuardrailResults[0].Message != "safe pre-stage result" {
			t.Fatalf("pre-stage diagnostics changed: metadata=%v results=%+v", rc.Metadata, rc.GuardrailResults)
		}
		for _, diagnostic := range rc.GuardrailResults[1:] {
			if diagnostic.Message != "response check failed" {
				t.Fatalf("unsafe post-stage diagnostic: %+v", diagnostic)
			}
		}
		rc.Release()
	}
}
