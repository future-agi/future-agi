package routing

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func autoInfo(inPerM, outPerM float64, caps modeldb.CapabilityFlags) *modeldb.ModelInfo {
	return &modeldb.ModelInfo{
		Mode:            modeldb.ModeChat,
		MaxInputTokens:  200000,
		MaxOutputTokens: 32000,
		Pricing:         modeldb.PricingInfo{InputPerToken: inPerM / 1e6, OutputPerToken: outPerM / 1e6},
		Capabilities:    caps,
	}
}

var allCaps = modeldb.CapabilityFlags{FunctionCalling: true, Vision: true, ResponseSchema: true, Reasoning: true, Streaming: true}

func autoPool() []AutoCandidate {
	return []AutoCandidate{
		{Model: "vendor/nano", ProviderID: "or", Info: autoInfo(0.05, 0.2, modeldb.CapabilityFlags{})},
		{Model: "vendor/mini", ProviderID: "or", Info: autoInfo(0.3, 1.2, allCaps)},
		{Model: "vendor/mid", ProviderID: "or", Info: autoInfo(1, 4, allCaps)},
		{Model: "vendor/frontier", ProviderID: "or", Info: autoInfo(5, 25, allCaps)},
	}
}

func userMsg(text string) models.Message {
	b, _ := json.Marshal(text)
	return models.Message{Role: "user", Content: b}
}

func TestChooseAutoSimplePromptGoesEconomical(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("hi, what's the capital of France?")}}
	d, err := ChooseAuto(req, autoPool(), AutoPolicy{})
	if err != nil {
		t.Fatal(err)
	}
	if d.TaskTier != "simple" || d.Model != "vendor/nano" {
		t.Fatalf("simple prompt chose %s (tier %s, complexity %d)", d.Model, d.TaskTier, d.Complexity)
	}
	if len(d.Fallbacks) != maxAutoFallbacks {
		t.Errorf("fallbacks = %v", d.Fallbacks)
	}
}

func TestChooseAutoComplexPromptGoesFrontier(t *testing.T) {
	long := strings.Repeat("Analyze and compare these designs step by step, then prove which one is correct. ", 120)
	req := &models.ChatCompletionRequest{
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"` + strings.Repeat("You are a meticulous reviewer. ", 80) + `"`)},
			userMsg(long), {Role: "assistant", Content: json.RawMessage(`"ok"`)}, userMsg(long),
		},
		Tools:          []models.Tool{{Type: "function"}, {Type: "function"}, {Type: "function"}},
		ResponseFormat: &models.ResponseFormat{Type: "json_schema"},
	}
	d, err := ChooseAuto(req, autoPool(), AutoPolicy{})
	if err != nil {
		t.Fatal(err)
	}
	if d.TaskTier != "complex" || d.Model != "vendor/frontier" {
		t.Fatalf("complex prompt chose %s (tier %s, complexity %d)", d.Model, d.TaskTier, d.Complexity)
	}
	if !contains(d.Needs, "tools") || !contains(d.Needs, "structured output") {
		t.Errorf("needs = %v", d.Needs)
	}
	if !rejected(d, "vendor/nano") {
		t.Error("a model without tool support must be rejected for a tool request")
	}
}

func TestChooseAutoPreferenceShiftsTheTier(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("hello")}}
	cheap, _ := ChooseAuto(req, autoPool(), AutoPolicy{Preference: AutoPreferCost})
	quality, _ := ChooseAuto(req, autoPool(), AutoPolicy{Preference: AutoPreferQuality})
	if cheap.Model != "vendor/nano" {
		t.Errorf("cost preference chose %s", cheap.Model)
	}
	if quality.Model == "vendor/nano" {
		t.Error("quality preference should move a simple prompt off the cheapest model")
	}
}

func TestChooseAutoAppliesPriceCapsAndHealth(t *testing.T) {
	pool := autoPool()
	pool[2].Health = &ProviderHealth{CircuitState: "open"}
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("hello")}, Tools: []models.Tool{{Type: "function"}}}
	d, err := ChooseAuto(req, pool, AutoPolicy{MaxOutputPerMTok: 10, Preference: AutoPreferQuality})
	if err != nil {
		t.Fatal(err)
	}
	if d.Model != "vendor/mini" {
		t.Fatalf("chose %s; frontier is over the cap and mid's circuit is open", d.Model)
	}
	if !rejected(d, "vendor/frontier") || !rejected(d, "vendor/mid") {
		t.Errorf("rejections = %+v", d.Rejected)
	}
}

func TestChooseAutoRespectsContextAndOutputLimits(t *testing.T) {
	pool := autoPool()
	pool[3].Info.MaxInputTokens = 10
	max := 64000
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg(strings.Repeat("word ", 50))}, MaxTokens: &max}
	_, err := ChooseAuto(req, pool, AutoPolicy{})
	var none *NoAutoCandidateError
	if !errors.As(err, &none) || len(none.Rejected) != 4 {
		t.Fatalf("want every model rejected (output cap 32000, frontier context 10), got %v", err)
	}
}

func TestChooseAutoUnknownModelsAreNotEligible(t *testing.T) {
	req := &models.ChatCompletionRequest{Messages: []models.Message{userMsg("hello")}}
	_, err := ChooseAuto(req, []AutoCandidate{{Model: "mystery", ProviderID: "x"}}, AutoPolicy{})
	if err == nil {
		t.Fatal("a model with no data must not be chosen blind")
	}
}

// The tier each common request shape lands in. A change to the complexity
// weights or the tier thresholds that moves one of these changes which model
// auto picks for that traffic.
func TestAutoTaskTierOfRealisticRequests(t *testing.T) {
	tools := func(n int) []models.Tool { return make([]models.Tool, n) }
	maxTokens := 1024
	agentTurns := []models.Message{{Role: "system", Content: json.RawMessage(`"` + strings.Repeat("You are an agent that uses tools carefully. ", 30) + `"`)}}
	for i := 0; i < 7; i++ {
		agentTurns = append(agentTurns, userMsg(strings.Repeat("look up the order status and refund policy ", 6)))
	}
	cases := []struct {
		name string
		req  *models.ChatCompletionRequest
		want string
	}{
		{"one-line question", &models.ChatCompletionRequest{Messages: []models.Message{userMsg("What's a good name for a coffee shop?")}}, "simple"},
		{"six-turn chat with a system prompt", &models.ChatCompletionRequest{Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"` + strings.Repeat("You are a helpful assistant. ", 10) + `"`)},
			userMsg("hi"), {Role: "assistant", Content: json.RawMessage(`"hello"`)}, userMsg("explain recursion"),
			{Role: "assistant", Content: json.RawMessage(`"` + strings.Repeat("Recursion is ", 40) + `"`)}, userMsg("give an example in python"),
		}}, "standard"},
		{"structured extraction", &models.ChatCompletionRequest{
			Messages:       []models.Message{userMsg(strings.Repeat("Invoice line item text. ", 60))},
			ResponseFormat: &models.ResponseFormat{Type: "json_schema"}, MaxTokens: &maxTokens,
		}, "standard"},
		{"grounded answer over 20k characters", &models.ChatCompletionRequest{Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"Answer only from the context."`)},
			userMsg(strings.Repeat("Context paragraph about the product. ", 540) + " Question: summarise the warranty terms."),
		}}, "complex"},
		{"analysis", &models.ChatCompletionRequest{Messages: []models.Message{
			userMsg("Analyze and compare these two architectures step by step and explain why one is better: " + strings.Repeat("design detail ", 350)),
		}}, "complex"},
		{"tool-using agent", &models.ChatCompletionRequest{Messages: agentTurns, Tools: tools(4)}, "complex"},
	}
	for _, c := range cases {
		if got := taskTier(ScoreComplexity(c.req)); got != c.want {
			t.Errorf("%s: tier %s (complexity %d), want %s", c.name, got, ScoreComplexity(c.req), c.want)
		}
	}
}

func TestIsAutoModel(t *testing.T) {
	for _, m := range []string{"auto", "agentcc/auto"} {
		if !IsAutoModel(m) {
			t.Errorf("%q should be auto", m)
		}
	}
	if IsAutoModel("openrouter/auto") {
		t.Error("openrouter/auto is OpenRouter's own router and must pass through")
	}
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

func rejected(d *AutoDecision, model string) bool {
	for _, r := range d.Rejected {
		if r.Model == model {
			return true
		}
	}
	return false
}
