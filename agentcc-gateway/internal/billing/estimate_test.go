package billing

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func intp(v int) *int { return &v }

// helloRequest is "hello world" from the user: 3 message overhead + 1 role +
// 3 content + 3 reply primer = 10 input tokens.
func helloRequest(maxTokens *int) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Model:     "vendor/model",
		Messages:  []models.Message{{Role: "user", Content: json.RawMessage(`"hello world"`)}},
		MaxTokens: maxTokens,
	}
}

func TestEstimateWorstCaseExactToTheMicro(t *testing.T) {
	req := helloRequest(intp(1000))
	if n := EstimateInputTokens(req); n != 10 {
		t.Fatalf("input tokens = %d, want 10; the fixtures below assume it", n)
	}
	card := models.PriceCard{Prompt: 3e-6, Completion: 15e-6, Request: 0.0001}
	for _, tc := range []struct {
		name      string
		markupBps int
		want      int64
	}{
		// 10 × 3 + 1000 × 15 + 100 = 15,130 micros upstream.
		{"no markup is exact, never one micro over", 0, 15130},
		// 15,130 × 1.05 = 15,886.5, rounded up.
		{"5% markup rounds a half micro up", 500, 15887},
		// 15,130 × 1.10 = 16,643 exactly.
		{"10% markup lands on a whole micro", 1000, 16643},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := EstimateWorstCaseMicros(req, card, tc.markupBps, 4096); got != tc.want {
				t.Fatalf("estimate = %d micros, want %d", got, tc.want)
			}
		})
	}
}

func TestEstimateOutputCap(t *testing.T) {
	card := models.PriceCard{Completion: 1e-6}
	req := helloRequest(nil)
	if got := EstimateWorstCaseMicros(req, card, 0, 4096); got != 4096 {
		t.Errorf("no max_tokens: estimate = %d, want the default output of 4096 tokens", got)
	}
	req.MaxCompletionTokens = intp(700)
	if got := EstimateWorstCaseMicros(req, card, 0, 4096); got != 700 {
		t.Errorf("max_completion_tokens 700: estimate = %d", got)
	}
	req.MaxTokens = intp(900)
	if got := EstimateWorstCaseMicros(req, card, 0, 4096); got != 900 {
		t.Errorf("both set: estimate = %d, want the larger (900)", got)
	}
	if got := DefaultMaxOut(2000, 4096); got != 2000 {
		t.Errorf("DefaultMaxOut(model 2000, configured 4096) = %d, want 2000", got)
	}
	if got := DefaultMaxOut(0, 4096); got != 4096 {
		t.Errorf("DefaultMaxOut(unknown model max, 4096) = %d, want 4096", got)
	}
	if got := DefaultMaxOut(64000, 4096); got != 4096 {
		t.Errorf("DefaultMaxOut(64000, 4096) = %d, want 4096", got)
	}
}

func TestEstimateCountsToolDefinitions(t *testing.T) {
	card := models.PriceCard{Prompt: 1e-6}
	req := helloRequest(intp(1))
	without := EstimateWorstCaseMicros(req, card, 0, 0)
	req.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "lookup", Description: "find a record by its id in the archive"}}}
	if with := EstimateWorstCaseMicros(req, card, 0, 0); with <= without {
		t.Fatalf("a tool definition added no input: %d vs %d", with, without)
	}
}

func TestHighestCardInEndpointSet(t *testing.T) {
	req := helloRequest(intp(100))
	set := []models.EndpointRef{
		{ID: "a", UpstreamProvider: "a", Price: models.PriceCard{Prompt: 1e-6, Completion: 1e-6}},
		{ID: "c", UpstreamProvider: "c", Price: models.PriceCard{Prompt: 3e-6, Completion: 3e-6}},
		{ID: "b", UpstreamProvider: "b", Price: models.PriceCard{Prompt: 2e-6, Completion: 2e-6}},
	}
	card, micros, ok := HighestCard(set, req, 500, 4096)
	if !ok || card != set[1].Price {
		t.Fatalf("card = %+v ok %v, want endpoint c's", card, ok)
	}
	if want := EstimateWorstCaseMicros(req, set[1].Price, 500, 4096); micros != want {
		t.Fatalf("micros = %d, want %d", micros, want)
	}
	if _, _, ok := HighestCard(nil, req, 500, 4096); ok {
		t.Fatal("an empty set has no card")
	}
}

func TestUSDToMicrosRoundsHalfUp(t *testing.T) {
	for usd, want := range map[float64]int64{0.10: 100000, 0.0000025: 3, 0.04: 40000, -1: 0} {
		if got := USDToMicros(usd); got != want {
			t.Errorf("USDToMicros(%v) = %d, want %d", usd, got, want)
		}
	}
}
