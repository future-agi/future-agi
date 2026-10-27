package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func chunk(content string, finish string) models.StreamChunk {
	c := models.StreamChoice{Index: 0}
	if content != "" {
		c.Delta.Content = &content
	}
	if finish != "" {
		c.FinishReason = &finish
	}
	return models.StreamChunk{Choices: []models.StreamChoice{c}}
}

// finalizeStream builds rc.Response from usage alone, so without this assembly
// a streamed request reaches every post-plugin with no completion at all.
func TestStreamCaptureAssemblesCompletion(t *testing.T) {
	c := newStreamCapture(true)
	c.observe(chunk("Hel", ""))
	c.observe(chunk("lo ", ""))
	c.observe(chunk("world", ""))
	c.observe(chunk("", "stop"))

	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)

	if len(resp.Choices) != 1 {
		t.Fatalf("got %d choices, want 1", len(resp.Choices))
	}
	var got string
	if err := json.Unmarshal(resp.Choices[0].Message.Content, &got); err != nil {
		t.Fatalf("content is not a JSON string: %v", err)
	}
	if got != "Hello world" {
		t.Errorf("content = %q, want %q", got, "Hello world")
	}
	if resp.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q", resp.Choices[0].FinishReason)
	}
	if resp.Choices[0].Message.Role != "assistant" {
		t.Errorf("role = %q", resp.Choices[0].Message.Role)
	}
}

// Disabled must cost nothing and leave the response exactly as it was.
func TestStreamCaptureDisabledIsInert(t *testing.T) {
	c := newStreamCapture(false)
	c.observe(chunk("ignored", "stop")) // must not panic on nil
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if resp.Choices != nil {
		t.Errorf("disabled capture wrote choices: %v", resp.Choices)
	}
}

// A stream that produced nothing must not be reported as an empty completion —
// absent and empty mean different things to whatever reads the span.
func TestStreamCaptureNoContentLeavesResponseAlone(t *testing.T) {
	c := newStreamCapture(true)
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if resp.Choices != nil {
		t.Errorf("wrote choices for a stream with no deltas: %v", resp.Choices)
	}
}

func TestStreamCaptureBounded(t *testing.T) {
	c := newStreamCapture(true)
	big := strings.Repeat("x", 1<<20)
	for i := 0; i < 4; i++ {
		c.observe(chunk(big, ""))
	}
	c.observe(chunk("", "length"))

	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	var got string
	_ = json.Unmarshal(resp.Choices[0].Message.Content, &got)
	if len(got) > maxStreamCaptureBytes {
		t.Errorf("captured %d bytes, over the %d cap", len(got), maxStreamCaptureBytes)
	}
	if !c.truncated {
		t.Error("truncation not recorded")
	}
	// The finish reason still lands even after content stopped accumulating.
	if resp.Choices[0].FinishReason != "length" {
		t.Errorf("finish_reason = %q", resp.Choices[0].FinishReason)
	}
}

// Tool-call deltas are deliberately not assembled; a half-built arguments
// string would be worse than none.
func TestStreamCaptureIgnoresToolCallDeltas(t *testing.T) {
	c := newStreamCapture(true)
	ch := models.StreamChunk{Choices: []models.StreamChoice{{
		Index: 0,
		Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Index: 0, ID: "call_1"}}},
	}}}
	c.observe(ch)

	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if resp.Choices != nil {
		t.Errorf("assembled a completion from tool-call deltas alone: %v", resp.Choices)
	}
}
