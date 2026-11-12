package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func walkToolChunk(choice, index int, id, name, args, finish string) models.StreamChunk {
	sc := models.StreamChoice{Index: choice, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Index: index, ID: id, Function: &models.FunctionCall{Name: name, Arguments: args}}}}}
	if id != "" {
		sc.Delta.ToolCalls[0].Type = "function"
	}
	if finish != "" {
		sc.FinishReason = &finish
	}
	return models.StreamChunk{Choices: []models.StreamChoice{sc}}
}

func TestWalkStreamCaptureCompleteTools(t *testing.T) {
	c := newStreamCapture(true)
	c.observe(walkToolChunk(1, 0, "call_other", "other", "{}", "tool_calls"))
	c.observe(walkToolChunk(0, 1, "call_weather", "get_", `{"city":`, ""))
	c.observe(walkToolChunk(0, 0, "call_clock", "clock", "{}", ""))
	c.observe(walkToolChunk(0, 1, "", "weather", `"Paris"}`, "tool_calls"))
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if len(resp.Choices) != 2 {
		t.Fatalf("choices = %+v", resp.Choices)
	}
	if len(resp.Choices[0].Message.ToolCalls) != 2 {
		t.Fatalf("tool calls disappeared from captured response: %+v", resp.Choices[0])
	}
	first := resp.Choices[0].Message.ToolCalls
	if first[0].ID != "call_clock" || first[1].ID != "call_weather" || first[1].Type != "function" || first[1].Function.Name != "get_weather" || first[1].Function.Arguments != `{"city":"Paris"}` {
		t.Fatalf("bad tool assembly: %+v", first)
	}
	if resp.Choices[1].Message.ToolCalls[0].ID != "call_other" {
		t.Fatal("choice indexes interleaved")
	}
	if resp.Choices[0].FinishReason != "tool_calls" {
		t.Fatal("finish reason lost")
	}
	if !c.complete() {
		t.Fatal("complete tool stream marked incomplete")
	}
	raw, err := json.Marshal(resp)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), `"agentcc_capture_incomplete":true`) {
		t.Fatalf("complete response marked incomplete: %s", raw)
	}
	if !strings.Contains(string(raw), `"content":null`) {
		t.Fatalf("tool-only response should have null content: %s", raw)
	}
}

func TestWalkStreamCaptureIncompleteTools(t *testing.T) {
	for _, tc := range []struct {
		name   string
		finish string
		args   string
	}{
		{"disconnect", "", `{"city":`},
		{"length", "length", `{"city":`},
		{"bound", "tool_calls", strings.Repeat("x", maxStreamCaptureBytes+1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := newStreamCapture(true)
			c.observe(walkToolChunk(0, 0, "call_weather", "get_weather", tc.args, tc.finish))
			resp := &models.ChatCompletionResponse{}
			c.applyTo(resp)
			raw, err := json.Marshal(resp)
			if err != nil {
				t.Fatal(err)
			}
			if string(resp.Extra["agentcc_capture_incomplete"]) != "true" {
				t.Fatalf("partial tool body presented without warning: %s", raw)
			}
			if c.size > maxStreamCaptureBytes {
				t.Fatalf("capture grew past bound: %d", c.size)
			}
		})
	}
}

func TestWalkStreamCaptureToolCountBound(t *testing.T) {
	c := newStreamCapture(true)
	for i := 0; i < 129; i++ {
		c.observe(walkToolChunk(0, i, "call_fixture", "f", "{}", ""))
	}
	c.observe(chunk("", "tool_calls"))
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if string(resp.Extra["agentcc_capture_incomplete"]) != "true" {
		t.Fatal("too many calls not marked incomplete")
	}
	if len(resp.Choices) != 1 || len(resp.Choices[0].Message.ToolCalls) > 128 {
		t.Fatalf("tool count not bounded: %+v", resp.Choices)
	}
}

func TestWalkStreamCaptureArgumentsShareTextBudget(t *testing.T) {
	c := newStreamCapture(true)
	c.observe(chunk(strings.Repeat("x", maxStreamCaptureBytes-10), ""))
	c.observe(walkToolChunk(0, 0, "call_fixture", "get_weather", "{}", "tool_calls"))
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if !c.truncated || string(resp.Extra["agentcc_capture_incomplete"]) != "true" {
		t.Fatal("tool metadata escaped shared capture budget")
	}
}
