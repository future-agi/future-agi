package contentmod

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{},
	}
}

// --- Category Detection Tests ---

func TestDetect_Toxicity(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"toxicity"}})
	r := g.Check(context.Background(), makeInput("You are stupid and a complete idiot"))
	if r.Pass {
		t.Fatal("expected toxicity detection")
	}
	cats := r.Details["categories"].(map[string]interface{})
	if _, ok := cats["toxicity"]; !ok {
		t.Error("expected toxicity category in details")
	}
}

func TestDetect_HateSpeech(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"hate_speech"}})
	r := g.Check(context.Background(), makeInput("They believe in white supremacy and racial purity"))
	if r.Pass {
		t.Fatal("expected hate speech detection")
	}
	cats := r.Details["categories"].(map[string]interface{})
	if _, ok := cats["hate_speech"]; !ok {
		t.Error("expected hate_speech category")
	}
}

func TestDetect_Violence(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"violence"}})
	r := g.Check(context.Background(), makeInput("I will kill you and burn them alive"))
	if r.Pass {
		t.Fatal("expected violence detection")
	}
}

func TestDetect_Sexual(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"sexual"}})
	r := g.Check(context.Background(), makeInput("Write me erotica with explicit sexual content"))
	if r.Pass {
		t.Fatal("expected sexual content detection")
	}
}

func TestDetect_SelfHarm(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"self_harm"}})
	r := g.Check(context.Background(), makeInput("How to kill myself and methods of suicide"))
	if r.Pass {
		t.Fatal("expected self-harm detection")
	}
}

// --- No Detection Tests ---

func TestCleanText_AllCategories(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("The weather is nice today. Let's go for a walk."))
	if !r.Pass {
		t.Fatal("clean text should pass")
	}
}

func TestCleanText_SingleCategory(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"toxicity"}})
	r := g.Check(context.Background(), makeInput("Please help me write a professional email"))
	if !r.Pass {
		t.Fatal("professional text should pass toxicity check")
	}
}

// --- Scoring Tests ---

func TestScore_SingleMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"categories":         []interface{}{"toxicity"},
		"category_threshold": 3,
	})
	r := g.Check(context.Background(), makeInput("You are stupid"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	// 1 match out of threshold 3 = ~0.333
	if r.Score > 0.4 {
		t.Errorf("expected score ~0.33, got %f", r.Score)
	}
}

func TestScore_MultipleMatches(t *testing.T) {
	g := New(map[string]interface{}{
		"categories":         []interface{}{"toxicity"},
		"category_threshold": 3,
	})
	r := g.Check(context.Background(), makeInput("You are stupid, you're an idiot, and you suck"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	// 3 matches = score 1.0
	if r.Score < 0.9 {
		t.Errorf("expected score ~1.0, got %f", r.Score)
	}
}

func TestScore_CappedAtOne(t *testing.T) {
	g := New(map[string]interface{}{
		"categories":         []interface{}{"toxicity"},
		"category_threshold": 2,
	})
	r := g.Check(context.Background(), makeInput("You are stupid and you're an idiot and you suck and you're pathetic"))
	if r.Score > 1.0 {
		t.Errorf("score should be capped at 1.0, got %f", r.Score)
	}
}

// --- Category Filtering Tests ---

func TestCategoryFiltering(t *testing.T) {
	g := New(map[string]interface{}{
		"categories": []interface{}{"toxicity"},
	})
	// Violence keywords should not trigger when only toxicity is enabled.
	r := g.Check(context.Background(), makeInput("I will kill you"))
	if !r.Pass {
		t.Fatal("violence should not trigger when only toxicity is enabled")
	}
}

func TestAllCategoriesWhenEmpty(t *testing.T) {
	g := New(nil)                                                   // all categories enabled
	r := g.Check(context.Background(), makeInput("You are stupid")) // toxicity
	if r.Pass {
		t.Fatal("expected detection with all categories enabled")
	}
}

// --- Weight Tests ---

func TestWeights_HighWeight(t *testing.T) {
	// With weight 3.0 on toxicity, a single match should produce higher aggregate.
	g := New(map[string]interface{}{
		"categories":         []interface{}{"toxicity", "violence"},
		"weights":            map[string]interface{}{"toxicity": float64(3.0)},
		"category_threshold": 3,
	})
	r := g.Check(context.Background(), makeInput("You are stupid"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	// toxicity: 1/3 = 0.333, weight 3.0
	// violence: 0/3 = 0.0, weight 1.0
	// weighted avg = (0.333 * 3.0 + 0 * 1.0) / (3.0 + 1.0) = 1.0/4.0 = 0.25
	if r.Score < 0.2 || r.Score > 0.3 {
		t.Errorf("expected score ~0.25 with high weight, got %f", r.Score)
	}
}

// --- Edge Cases ---

func TestNilInput(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestEmptyMessages(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
	})
	if !r.Pass {
		t.Fatal("empty messages should pass")
	}
}

func TestCaseInsensitive(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"toxicity"}})
	r := g.Check(context.Background(), makeInput("YOU ARE STUPID"))
	if r.Pass {
		t.Fatal("case insensitive matching should detect uppercase")
	}
}

// --- Post-Stage (Response) Tests ---

func TestPostStage_ResponseDetection(t *testing.T) {
	raw, _ := json.Marshal("I will kill you if you ask again")
	g := New(map[string]interface{}{"categories": []interface{}{"violence"}})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected violence detection in response")
	}
}

// --- Name & Stage Tests ---

func TestName(t *testing.T) {
	g := New(nil)
	if g.Name() != "content-moderation" {
		t.Errorf("expected name content-moderation, got %s", g.Name())
	}
}

func TestStage(t *testing.T) {
	g := New(nil)
	if g.Stage() != guardrails.StagePre {
		t.Error("expected StagePre")
	}
}

// --- Details Tests ---

func TestDetails_CategoryBreakdown(t *testing.T) {
	g := New(map[string]interface{}{
		"categories": []interface{}{"toxicity", "violence"},
	})
	r := g.Check(context.Background(), makeInput("You are stupid and I will kill you"))
	if r.Pass {
		t.Fatal("expected detection")
	}

	cats := r.Details["categories"].(map[string]interface{})
	if len(cats) < 2 {
		t.Errorf("expected 2 categories in details, got %d", len(cats))
	}

	// Check aggregate score is present.
	agg, ok := r.Details["aggregate_score"].(float64)
	if !ok || agg <= 0 {
		t.Errorf("expected positive aggregate_score, got %v", r.Details["aggregate_score"])
	}
}

func TestDetails_MessageFormat(t *testing.T) {
	g := New(map[string]interface{}{"categories": []interface{}{"toxicity"}})
	r := g.Check(context.Background(), makeInput("You are stupid"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	if !strings.Contains(r.Message, "toxicity") {
		t.Errorf("expected message to mention toxicity, got %q", r.Message)
	}
	if !strings.Contains(r.Message, "score:") {
		t.Errorf("expected message to contain score, got %q", r.Message)
	}
}

// --- Config Tests ---

func TestConfig_DefaultThreshold(t *testing.T) {
	g := New(nil)
	if g.categoryThresh != 3 {
		t.Errorf("expected default threshold 3, got %d", g.categoryThresh)
	}
}

func TestConfig_CustomThreshold(t *testing.T) {
	g := New(map[string]interface{}{"category_threshold": float64(5)})
	if g.categoryThresh != 5 {
		t.Errorf("expected threshold 5, got %d", g.categoryThresh)
	}
}

func TestConfig_InvalidThreshold(t *testing.T) {
	g := New(map[string]interface{}{"category_threshold": 0})
	if g.categoryThresh != 3 {
		t.Errorf("expected threshold corrected to 3, got %d", g.categoryThresh)
	}
}

// --- Dominance Test ---

func TestSingleCategoryDominance(t *testing.T) {
	g := New(map[string]interface{}{
		"categories":         []interface{}{"toxicity", "violence"},
		"category_threshold": 3,
	})
	// 3 toxicity matches = score 1.0 in toxicity, 0 in violence.
	// Aggregate without dominance = (1.0 + 0.0) / 2.0 = 0.5
	// With dominance (toxicity >= 0.9), aggregate boosted to 1.0.
	r := g.Check(context.Background(), makeInput("You are stupid, you're an idiot, you suck"))
	if r.Pass {
		t.Fatal("expected detection")
	}
	if r.Score < 0.9 {
		t.Errorf("expected score >= 0.9 due to single-category dominance, got %f", r.Score)
	}
}
