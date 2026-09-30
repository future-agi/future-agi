package guardrails

import (
	"context"
	"encoding/json"
	"slices"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// stagedGuardrail is a mockGuardrail an org check may move to the stages in
// supports.
type stagedGuardrail struct {
	mockGuardrail
	supports []Stage
}

func (g *stagedGuardrail) SupportsStage(s Stage) bool { return slices.Contains(g.supports, s) }

func stageTestResponse(text string) *models.ChatCompletionResponse {
	content, _ := json.Marshal(text)
	return &models.ChatCompletionResponse{
		ID:      "resp",
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: content}}},
	}
}

func TestRunsAtStage(t *testing.T) {
	fixedPre := &mockGuardrail{stage: StagePre}
	fixedPost := &mockGuardrail{stage: StagePost}
	either := &stagedGuardrail{mockGuardrail: mockGuardrail{stage: StagePre}, supports: []Stage{StagePre, StagePost}}
	postOnly := &stagedGuardrail{mockGuardrail: mockGuardrail{stage: StagePre}, supports: []Stage{StagePost}}

	tests := []struct {
		name       string
		g          Guardrail
		configured string
		pre, post  bool
	}{
		{"fixed pre ignores post", fixedPre, "post", true, false},
		{"fixed post ignores pre", fixedPost, "pre", false, true},
		{"fixed post ignores both", fixedPost, "both", false, true},
		{"unset keeps Stage", either, "", true, false},
		{"pre", either, "pre", true, false},
		{"post", either, "post", false, true},
		{"both", either, "both", true, true},
		{"case and space ignored", either, " Both ", true, true},
		{"unknown keeps Stage", either, "after", true, false},
		{"supported stage honoured", postOnly, "post", false, true},
		{"unsupported stage in both keeps Stage", postOnly, "both", true, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := runsAtStage(tt.g, tt.configured, StagePre); got != tt.pre {
				t.Errorf("runs pre = %v, want %v", got, tt.pre)
			}
			if got := runsAtStage(tt.g, tt.configured, StagePost); got != tt.post {
				t.Errorf("runs post = %v, want %v", got, tt.post)
			}
		})
	}
}

func TestEngine_RuleStage(t *testing.T) {
	tests := []struct {
		name      string
		stage     string
		pre, post int
	}{
		{"pre", "pre", 1, 0},
		{"post", "post", 0, 1},
		{"both", "both", 1, 1},
		{"case and space ignored", " Both ", 1, 1},
		{"empty skipped", "", 0, 0},
		{"unknown skipped", "after", 0, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			calls := 0
			g := &countingGuardrail{name: "scanner", stage: StagePre, result: &CheckResult{Pass: true}, callCount: &calls}
			engine := NewEngine(config.GuardrailsConfig{
				Rules: []config.GuardrailRuleConfig{{Name: "scanner", Stage: tt.stage, Action: "block"}},
			}, map[string]Guardrail{"scanner": g})

			if engine.PreCount() != tt.pre || engine.PostCount() != tt.post {
				t.Fatalf("pre/post rules = %d/%d, want %d/%d", engine.PreCount(), engine.PostCount(), tt.pre, tt.post)
			}
			input := &CheckInput{
				Request:  &models.ChatCompletionRequest{Model: "gpt-4o"},
				Response: stageTestResponse("hello"),
			}
			engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
			engine.RunPost(context.Background(), input, nil, policy.RequestPolicyNone)
			if calls != tt.pre+tt.post {
				t.Errorf("checks = %d, want %d", calls, tt.pre+tt.post)
			}
		})
	}
}

func TestPlugin_StaticPostRuleNeedsOutput(t *testing.T) {
	calls := 0
	g := &countingGuardrail{name: "scanner", stage: StagePost, result: &CheckResult{Pass: true}, callCount: &calls}
	engine := NewEngine(config.GuardrailsConfig{
		DefaultTimeout: 5 * time.Second,
		Rules:          []config.GuardrailRuleConfig{{Name: "scanner", Stage: "post", Action: "block"}},
	}, map[string]Guardrail{"scanner": g})
	plugin := NewPlugin(engine, nil, nil, nil, nil)

	tests := []struct {
		name  string
		resp  *models.ChatCompletionResponse
		calls int
	}{
		{"no response", nil, 0},
		{"usage-only response", &models.ChatCompletionResponse{ID: "resp", Usage: &models.Usage{TotalTokens: 3}}, 0},
		{"model output", stageTestResponse("hello"), 1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			calls = 0
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
			rc.Response = tt.resp

			if res := plugin.ProcessResponse(context.Background(), rc); res.Action != pipeline.Continue || res.Error != nil {
				t.Fatalf("post pass = %+v, want continue", res)
			}
			if calls != tt.calls {
				t.Errorf("checks = %d, want %d", calls, tt.calls)
			}
		})
	}
}
