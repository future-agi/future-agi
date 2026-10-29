package guardrails

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// An output block removes the content and keeps the usage. The upstream
// billed the request, so the cost plugin, which runs after the guardrails,
// still prices it.
func TestOutputBlockKeepsTheUsagePriced(t *testing.T) {
	cfg := config.GuardrailsConfig{Enabled: true, FailOpen: true, DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: "always", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5}}}
	registry := map[string]Guardrail{"always": &mockGuardrail{name: "always", stage: StagePost,
		result: &CheckResult{Pass: false, Score: 1, Action: ActionBlock, Message: "blocked"}}}
	db := modeldb.New(modeldb.BundledModels, nil)
	engine := pipeline.NewEngine(
		NewPlugin(NewEngine(cfg, registry), registry, nil, nil, nil),
		costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil),
	)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model, rc.Provider = "gpt-5.5", "openai"
	err := engine.Process(context.Background(), rc, func(_ context.Context, rc *models.RequestContext) error {
		rc.Response = &models.ChatCompletionResponse{
			Model:   "gpt-5.5",
			Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: []byte(`"blocked text"`)}}},
			Usage:   &models.Usage{PromptTokens: 1000, CompletionTokens: 100, TotalTokens: 1100},
		}
		return nil
	})
	if err == nil {
		t.Fatal("the response was not blocked")
	}
	if rc.Response == nil || len(rc.Response.Choices) != 0 {
		t.Fatalf("response after the block = %+v; want usage only", rc.Response)
	}
	if rc.Metadata["cost"] == "" {
		t.Fatalf("a blocked response recorded no cost; metadata %v", rc.Metadata)
	}
}
