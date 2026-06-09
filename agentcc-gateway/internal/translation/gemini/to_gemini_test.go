package gemini_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ── helpers ──────────────────────────────────────────────────────────────────

func makeTextResponse(text, finishReason, model string) *models.ChatCompletionResponse {
	content, _ := json.Marshal(text)
	return &models.ChatCompletionResponse{
		ID:      "resp-1",
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   model,
		Choices: []models.Choice{
			{
				Index: 0,
				Message: models.Message{
					Role:    "assistant",
					Content: json.RawMessage(content),
				},
				FinishReason: finishReason,
			},
		},
	}
}

// geminiRespFromJSON is a helper to decode Gemini response JSON for assertions.
type geminiRespDecoded struct {
	Candidates []struct {
		Content struct {
			Role  string `json:"role"`
			Parts []struct {
				Text         string `json:"text"`
				FunctionCall *struct {
					Name string          `json:"name"`
					Args json.RawMessage `json:"args"`
				} `json:"functionCall"`
			} `json:"parts"`
		} `json:"content"`
		FinishReason string `json:"finishReason"`
	} `json:"candidates"`
	UsageMetadata *struct {
		PromptTokenCount     int `json:"promptTokenCount"`
		CandidatesTokenCount int `json:"candidatesTokenCount"`
		TotalTokenCount      int `json:"totalTokenCount"`
	} `json:"usageMetadata"`
	ModelVersion string `json:"modelVersion"`
}

// ── tests ────────────────────────────────────────────────────────────────────

func TestResponseFromCanonical_SimpleText(t *testing.T) {
	resp := makeTextResponse("Hello!", "stop", "gemini-2.0-flash")
	tr := translator()
	b, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var gr geminiRespDecoded
	if err := json.Unmarshal(b, &gr); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if len(gr.Candidates) != 1 {
		t.Fatalf("expected 1 candidate, got %d", len(gr.Candidates))
	}
	cand := gr.Candidates[0]
	if cand.Content.Role != "model" {
		t.Errorf("candidate role: got %q, want %q", cand.Content.Role, "model")
	}
	if len(cand.Content.Parts) == 0 || cand.Content.Parts[0].Text != "Hello!" {
		t.Errorf("candidate text: got %+v", cand.Content.Parts)
	}
	if cand.FinishReason != "STOP" {
		t.Errorf("finishReason: got %q, want %q", cand.FinishReason, "STOP")
	}
	if gr.ModelVersion != "gemini-2.0-flash" {
		t.Errorf("modelVersion: got %q, want %q", gr.ModelVersion, "gemini-2.0-flash")
	}
}

func TestResponseFromCanonical_FinishReasonMapping(t *testing.T) {
	cases := []struct {
		openai string
		gemini string
	}{
		{"stop", "STOP"},
		{"length", "MAX_TOKENS"},
		{"tool_calls", "STOP"},
		{"content_filter", "SAFETY"},
	}
	tr := translator()
	for _, c := range cases {
		resp := makeTextResponse("text", c.openai, "gemini-2.0-flash")
		b, err := tr.ResponseFromCanonical(resp)
		if err != nil {
			t.Fatalf("openai=%q: unexpected error: %v", c.openai, err)
		}
		var gr geminiRespDecoded
		if err := json.Unmarshal(b, &gr); err != nil {
			t.Fatalf("openai=%q: decode error: %v", c.openai, err)
		}
		if len(gr.Candidates) == 0 {
			t.Fatalf("openai=%q: no candidates", c.openai)
		}
		if gr.Candidates[0].FinishReason != c.gemini {
			t.Errorf("openai=%q: finishReason got %q, want %q",
				c.openai, gr.Candidates[0].FinishReason, c.gemini)
		}
	}
}

func TestResponseFromCanonical_ToolCalls(t *testing.T) {
	resp := &models.ChatCompletionResponse{
		ID:      "resp-2",
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   "gemini-2.0-flash",
		Choices: []models.Choice{
			{
				Index: 0,
				Message: models.Message{
					Role: "assistant",
					ToolCalls: []models.ToolCall{
						{
							ID:   "call_abc",
							Type: "function",
							Function: models.FunctionCall{
								Name:      "get_weather",
								Arguments: `{"location":"London"}`,
							},
						},
					},
				},
				FinishReason: "tool_calls",
			},
		},
	}
	tr := translator()
	b, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var gr geminiRespDecoded
	if err := json.Unmarshal(b, &gr); err != nil {
		t.Fatalf("decode error: %v", err)
	}
	if len(gr.Candidates) == 0 {
		t.Fatal("no candidates")
	}
	parts := gr.Candidates[0].Content.Parts
	if len(parts) == 0 {
		t.Fatal("no parts")
	}
	fc := parts[0].FunctionCall
	if fc == nil {
		t.Fatal("expected functionCall part")
	}
	if fc.Name != "get_weather" {
		t.Errorf("functionCall name: got %q, want %q", fc.Name, "get_weather")
	}
	var args map[string]string
	if err := json.Unmarshal(fc.Args, &args); err != nil {
		t.Fatalf("decode args: %v", err)
	}
	if args["location"] != "London" {
		t.Errorf("args location: got %q, want %q", args["location"], "London")
	}
}

func TestResponseFromCanonical_UsageMetadata(t *testing.T) {
	resp := makeTextResponse("ok", "stop", "gemini-flash")
	resp.Usage = &models.Usage{
		PromptTokens:     10,
		CompletionTokens: 20,
		TotalTokens:      30,
	}
	tr := translator()
	b, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var gr geminiRespDecoded
	if err := json.Unmarshal(b, &gr); err != nil {
		t.Fatalf("decode error: %v", err)
	}
	if gr.UsageMetadata == nil {
		t.Fatal("expected usageMetadata to be set")
	}
	if gr.UsageMetadata.PromptTokenCount != 10 {
		t.Errorf("promptTokenCount: got %d, want 10", gr.UsageMetadata.PromptTokenCount)
	}
	if gr.UsageMetadata.CandidatesTokenCount != 20 {
		t.Errorf("candidatesTokenCount: got %d, want 20", gr.UsageMetadata.CandidatesTokenCount)
	}
	if gr.UsageMetadata.TotalTokenCount != 30 {
		t.Errorf("totalTokenCount: got %d, want 30", gr.UsageMetadata.TotalTokenCount)
	}
}

func TestResponseFromCanonical_ModelVersionStripping(t *testing.T) {
	resp := makeTextResponse("hi", "stop", "publishers/google/models/gemini-2.0-flash-001")
	tr := translator()
	b, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var gr geminiRespDecoded
	if err := json.Unmarshal(b, &gr); err != nil {
		t.Fatalf("decode error: %v", err)
	}
	if gr.ModelVersion != "gemini-2.0-flash-001" {
		t.Errorf("modelVersion: got %q, want %q", gr.ModelVersion, "gemini-2.0-flash-001")
	}
}

func TestResponseFromCanonical_EmptyChoices(t *testing.T) {
	resp := &models.ChatCompletionResponse{
		ID:      "resp-empty",
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   "gemini-2.0-flash",
		Choices: []models.Choice{},
	}
	tr := translator()
	b, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// Should produce valid JSON (no candidates is fine).
	var gr geminiRespDecoded
	if err := json.Unmarshal(b, &gr); err != nil {
		t.Fatalf("decode error: %v", err)
	}
}
