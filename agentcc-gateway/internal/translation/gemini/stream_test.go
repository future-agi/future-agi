package gemini_test

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ── helper ───────────────────────────────────────────────────────────────────

// drainStream runs StreamEventsFromCanonical over a slice of chunks and
// returns all emitted SSE event lines (the raw "data: {...}" strings).
func drainStream(t *testing.T, chunks []models.StreamChunk) []string {
	t.Helper()
	ch := make(chan models.StreamChunk, len(chunks))
	for _, c := range chunks {
		ch <- c
	}
	close(ch)

	tr := translator()
	outCh, errCh := tr.StreamEventsFromCanonical(context.Background(), ch)

	var events []string
	for b := range outCh {
		events = append(events, string(b))
	}
	if err, ok := <-errCh; ok && err != nil {
		t.Fatalf("stream error: %v", err)
	}
	return events
}

// parseGeminiSSE extracts the JSON payload from a "data: {...}\n\n" SSE line.
func parseGeminiSSE(t *testing.T, raw string) map[string]interface{} {
	t.Helper()
	raw = strings.TrimSpace(raw)
	if !strings.HasPrefix(raw, "data: ") {
		t.Fatalf("expected SSE data line, got: %q", raw)
	}
	jsonStr := strings.TrimPrefix(raw, "data: ")
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(jsonStr), &m); err != nil {
		t.Fatalf("failed to parse SSE payload: %v\npayload: %s", err, jsonStr)
	}
	return m
}

// ── tests ────────────────────────────────────────────────────────────────────

func TestStreamEventsFromCanonical_PureText(t *testing.T) {
	finish := "stop"
	chunks := []models.StreamChunk{
		{
			ID: "c1", Model: "gemini-2.0-flash",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{Content: strPtr("Hello")}},
			},
		},
		{
			ID: "c2", Model: "gemini-2.0-flash",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{Content: strPtr(", world")}, FinishReason: &finish},
			},
			Usage: &models.Usage{PromptTokens: 5, CompletionTokens: 3, TotalTokens: 8},
		},
	}

	events := drainStream(t, chunks)
	if len(events) != 2 {
		t.Fatalf("expected 2 events, got %d: %v", len(events), events)
	}

	// First event — just text, no finish
	m := parseGeminiSSE(t, events[0])
	candidates := getCandidates(t, m)
	if len(candidates) == 0 {
		t.Fatal("expected candidates in first event")
	}
	if getText(t, candidates[0]) != "Hello" {
		t.Errorf("first event text: got %q", getText(t, candidates[0]))
	}
	if getFinishReason(candidates[0]) != "" {
		t.Errorf("first event should have no finishReason")
	}

	// Second event — text + finishReason + usage
	m2 := parseGeminiSSE(t, events[1])
	candidates2 := getCandidates(t, m2)
	if len(candidates2) == 0 {
		t.Fatal("expected candidates in second event")
	}
	if getText(t, candidates2[0]) != ", world" {
		t.Errorf("second event text: got %q", getText(t, candidates2[0]))
	}
	if getFinishReason(candidates2[0]) != "STOP" {
		t.Errorf("second event finishReason: got %q, want STOP", getFinishReason(candidates2[0]))
	}
	// Usage
	usage, ok := m2["usageMetadata"].(map[string]interface{})
	if !ok {
		t.Fatal("expected usageMetadata in terminal event")
	}
	if int(usage["promptTokenCount"].(float64)) != 5 {
		t.Errorf("promptTokenCount: got %v, want 5", usage["promptTokenCount"])
	}
}

func TestStreamEventsFromCanonical_ToolCallAccumulation(t *testing.T) {
	// Simulate OpenAI streaming a tool call over 3 chunks:
	// chunk1: name
	// chunk2: first arg fragment
	// chunk3: second arg fragment + finish_reason
	finish := "tool_calls"
	chunks := []models.StreamChunk{
		{
			ID: "c1",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{
					ToolCalls: []models.ToolCallDelta{
						{Index: 0, ID: "call_x", Type: "function",
							Function: &models.FunctionCall{Name: "get_weather", Arguments: ""}},
					},
				}},
			},
		},
		{
			ID: "c2",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{
					ToolCalls: []models.ToolCallDelta{
						{Index: 0, Function: &models.FunctionCall{Arguments: `{"locati`}},
					},
				}},
			},
		},
		{
			ID: "c3",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{
					ToolCalls: []models.ToolCallDelta{
						{Index: 0, Function: &models.FunctionCall{Arguments: `on":"London"}`}},
					},
				}, FinishReason: &finish},
			},
		},
	}

	events := drainStream(t, chunks)
	if len(events) != 3 {
		t.Fatalf("expected 3 events (one per chunk), got %d", len(events))
	}

	// First two events: no functionCall parts yet (arguments not complete)
	for i := 0; i < 2; i++ {
		m := parseGeminiSSE(t, events[i])
		cands := getCandidates(t, m)
		if len(cands) > 0 {
			parts := getParts(t, cands[0])
			for _, p := range parts {
				pm := p.(map[string]interface{})
				if _, ok := pm["functionCall"]; ok {
					t.Errorf("event[%d]: should not have functionCall part before terminal chunk", i)
				}
			}
		}
	}

	// Terminal event (chunk3): should have the functionCall part
	mTerm := parseGeminiSSE(t, events[2])
	candsTerm := getCandidates(t, mTerm)
	if len(candsTerm) == 0 {
		t.Fatal("expected candidates in terminal event")
	}
	parts := getParts(t, candsTerm[0])
	foundFC := false
	for _, p := range parts {
		pm := p.(map[string]interface{})
		if fc, ok := pm["functionCall"].(map[string]interface{}); ok {
			foundFC = true
			if fc["name"] != "get_weather" {
				t.Errorf("functionCall name: got %q, want get_weather", fc["name"])
			}
			args, _ := fc["args"].(map[string]interface{})
			if args["location"] != "London" {
				t.Errorf("functionCall args: got %v, want location=London", args)
			}
		}
	}
	if !foundFC {
		t.Error("expected functionCall part in terminal event")
	}
	if getFinishReason(candsTerm[0]) != "STOP" {
		t.Errorf("terminal finishReason: got %q, want STOP", getFinishReason(candsTerm[0]))
	}
}

func TestStreamEventsFromCanonical_FinishReasonLength(t *testing.T) {
	finish := "length"
	chunks := []models.StreamChunk{
		{
			ID: "c1",
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{Content: strPtr("truncated")}, FinishReason: &finish},
			},
		},
	}
	events := drainStream(t, chunks)
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	m := parseGeminiSSE(t, events[0])
	cands := getCandidates(t, m)
	if len(cands) == 0 {
		t.Fatal("no candidates")
	}
	if getFinishReason(cands[0]) != "MAX_TOKENS" {
		t.Errorf("finishReason: got %q, want MAX_TOKENS", getFinishReason(cands[0]))
	}
}

func TestStreamEventsFromCanonical_EmptyStream(t *testing.T) {
	events := drainStream(t, nil)
	if len(events) != 0 {
		t.Errorf("expected 0 events for empty stream, got %d", len(events))
	}
}

func TestStreamEventsFromCanonical_UsageOnlyChunk(t *testing.T) {
	// Some providers emit a trailing chunk with only usage, no choices.
	chunks := []models.StreamChunk{
		{
			ID:    "c1",
			Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15},
		},
	}
	events := drainStream(t, chunks)
	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	m := parseGeminiSSE(t, events[0])
	if usage, ok := m["usageMetadata"].(map[string]interface{}); ok {
		if int(usage["totalTokenCount"].(float64)) != 15 {
			t.Errorf("totalTokenCount: got %v", usage["totalTokenCount"])
		}
	} else {
		t.Error("expected usageMetadata")
	}
}

// ── assertion helpers ─────────────────────────────────────────────────────────

func strPtr(s string) *string { return &s }

func getCandidates(t *testing.T, m map[string]interface{}) []interface{} {
	t.Helper()
	cands, ok := m["candidates"].([]interface{})
	if !ok {
		return nil
	}
	return cands
}

func getText(t *testing.T, cand interface{}) string {
	t.Helper()
	cm := cand.(map[string]interface{})
	content, ok := cm["content"].(map[string]interface{})
	if !ok {
		return ""
	}
	parts, ok := content["parts"].([]interface{})
	if !ok || len(parts) == 0 {
		return ""
	}
	pm := parts[0].(map[string]interface{})
	text, _ := pm["text"].(string)
	return text
}

func getParts(t *testing.T, cand interface{}) []interface{} {
	t.Helper()
	cm := cand.(map[string]interface{})
	content, ok := cm["content"].(map[string]interface{})
	if !ok {
		return nil
	}
	parts, _ := content["parts"].([]interface{})
	return parts
}

func getFinishReason(cand interface{}) string {
	cm := cand.(map[string]interface{})
	r, _ := cm["finishReason"].(string)
	return r
}
