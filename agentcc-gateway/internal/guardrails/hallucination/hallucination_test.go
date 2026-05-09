package hallucination

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeResponseInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Metadata: map[string]string{},
	}
}

func TestDetect_AIModelDisclaimer(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("As an AI language model, I don't have access to real-time data"))
	if r.Pass {
		t.Fatal("expected hallucination indicator detection")
	}
}

func TestDetect_FabricatedInfo(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("I should note that I may have hallucinated some details"))
	if r.Pass {
		t.Fatal("expected detection of self-admitted hallucination")
	}
}

func TestDetect_FictionalSource(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("According to a fictional study by Dr. Smith"))
	if r.Pass {
		t.Fatal("expected detection of fictional source")
	}
}

func TestCleanResponse(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput("The capital of France is Paris. It has a population of about 2 million in the city proper."))
	if !r.Pass {
		t.Fatal("clean response should pass")
	}
}

func TestNilResponse(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
	})
	if !r.Pass {
		t.Fatal("nil response should pass")
	}
}

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestName(t *testing.T) {
	if New(nil).Name() != "hallucination-detection" {
		t.Error("wrong name")
	}
}

func TestStage(t *testing.T) {
	if New(nil).Stage() != guardrails.StagePost {
		t.Error("expected StagePost")
	}
}

func TestMultipleIndicators(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeResponseInput(
		"As an AI language model, I don't have access to real-time data. "+
			"I should note that I may have hallucinated some details. "+
			"According to a fictional study by researchers."))
	if r.Pass {
		t.Fatal("expected detection")
	}
	if r.Score < 0.4 {
		t.Errorf("expected higher score for multiple indicators, got %f", r.Score)
	}
}
