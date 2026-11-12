package server

import (
	"encoding/json"
	"reflect"
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
	c.observe(walkToolChunk(0, 0, "call_ignored", "ignored", `{"private":"ignored"}`, ""))
	c.observe(walkToolChunk(0, 0, "", "", "", "tool_calls"))
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if resp.Choices != nil || resp.Extra != nil {
		t.Errorf("disabled capture changed response: %+v", resp)
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

// Even an ID-only delta is useful evidence when the stream disconnects. Keep
// only what arrived, with an incomplete marker rather than invented fields.
func TestStreamCaptureRetainsBareToolCallDeltas(t *testing.T) {
	c := newStreamCapture(true)
	ch := models.StreamChunk{Choices: []models.StreamChoice{{
		Index: 0,
		Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Index: 0, ID: "call_1"}}},
	}}}
	c.observe(ch)

	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	if len(resp.Choices) != 1 || len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("lost the partial tool call: %+v", resp.Choices)
	}
	got := resp.Choices[0].Message.ToolCalls[0]
	if !reflect.DeepEqual(got, models.ToolCall{ID: "call_1"}) {
		t.Errorf("partial tool = %+v, want only the observed ID", got)
	}
	if resp.Choices[0].FinishReason != "" || c.complete() || string(resp.Extra["agentcc_capture_incomplete"]) != "true" {
		t.Fatalf("unfinished call presented as complete: %+v", resp)
	}
}

func TestStreamCaptureRetainsUnfinishedToolChoices(t *testing.T) {
	c := newStreamCapture(true)
	c.observe(walkToolChunk(3, 0, "call_other", "other", "{}", "tool_calls"))
	c.observe(walkToolChunk(1, 4, "call_weather", "get_", `{"city":`, ""))
	c.observe(walkToolChunk(1, 2, "call_clock", "clock", "{}", ""))
	c.observe(walkToolChunk(1, 4, "", "weather", `"Paris`, ""))
	// No terminal chunk for choice 1: neither its calls nor its choice index
	// may disappear, and argument fragments must not leak across call indexes.
	resp := &models.ChatCompletionResponse{}
	c.applyTo(resp)
	want := []models.Choice{
		{Index: 1, Message: models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
			{ID: "call_clock", Type: "function", Function: models.FunctionCall{Name: "clock", Arguments: "{}"}},
			{ID: "call_weather", Type: "function", Function: models.FunctionCall{Name: "get_weather", Arguments: `{"city":"Paris`}},
		}}},
		{Index: 3, FinishReason: "tool_calls", Message: models.Message{Role: "assistant", ToolCalls: []models.ToolCall{
			{ID: "call_other", Type: "function", Function: models.FunctionCall{Name: "other", Arguments: "{}"}},
		}}},
	}
	if !reflect.DeepEqual(resp.Choices, want) {
		t.Fatalf("captured choices = %+v, want %+v", resp.Choices, want)
	}
	if c.complete() || string(resp.Extra["agentcc_capture_incomplete"]) != "true" {
		t.Fatalf("unfinished choice presented as complete: %+v", resp)
	}
}

func TestStreamCaptureToolArgumentsSurviveInterruptedStreams(t *testing.T) {
	const prefix = `{"city":"`
	for _, tc := range []struct {
		name       string
		finish     string
		tail       string
		bounded    bool
		incomplete bool
	}{
		{name: "disconnect", tail: "Paris", incomplete: true},
		{name: "length", finish: "length", tail: "Paris", incomplete: true},
		{name: "content_filter", finish: "content_filter", tail: "Paris", incomplete: true},
		{name: "bound_then_disconnect", tail: strings.Repeat("x", 64), bounded: true, incomplete: true},
		{name: "bound_then_finish", finish: "tool_calls", tail: strings.Repeat("x", 64), bounded: true, incomplete: true},
		{name: "complete", finish: "tool_calls", tail: `Paris"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := newStreamCapture(true)
			if tc.bounded {
				c.limit = 64
			}
			c.observe(walkToolChunk(0, 0, "call_1", "weather", prefix, ""))
			c.observe(walkToolChunk(0, 0, "", "", tc.tail, tc.finish))
			resp := &models.ChatCompletionResponse{}
			c.applyTo(resp)
			if len(resp.Choices) != 1 || len(resp.Choices[0].Message.ToolCalls) != 1 {
				t.Fatalf("lost captured call: %+v", resp.Choices)
			}
			choice := resp.Choices[0]
			wantArgs := prefix
			if !tc.bounded {
				wantArgs += tc.tail
			}
			want := models.ToolCall{ID: "call_1", Type: "function", Function: models.FunctionCall{Name: "weather", Arguments: wantArgs}}
			if got := choice.Message.ToolCalls[0]; !reflect.DeepEqual(got, want) {
				t.Errorf("captured call = %+v, want %+v", got, want)
			}
			if choice.FinishReason != tc.finish || choice.Message.Role != "assistant" {
				t.Errorf("synthesized choice = %+v", choice)
			}
			if c.complete() == tc.incomplete || (string(resp.Extra["agentcc_capture_incomplete"]) == "true") != tc.incomplete {
				t.Errorf("incomplete = %s, complete = %v, want incomplete %v", resp.Extra["agentcc_capture_incomplete"], c.complete(), tc.incomplete)
			}
			if c.truncated != tc.bounded || c.size > c.maxBytes() {
				t.Errorf("capture budget changed: size=%d limit=%d truncated=%v", c.size, c.maxBytes(), c.truncated)
			}
			// Partial arguments remain a string in the normal response shape;
			// do not repair JSON, invent a closing brace, or turn it into content.
			raw, err := json.Marshal(resp)
			if err != nil {
				t.Fatal(err)
			}
			var decoded models.ChatCompletionResponse
			if err := json.Unmarshal(raw, &decoded); err != nil {
				t.Fatal(err)
			}
			if len(decoded.Choices) != 1 || len(decoded.Choices[0].Message.ToolCalls) != 1 {
				t.Fatalf("wire body lost the call: %s", raw)
			}
			msg := decoded.Choices[0].Message
			if !reflect.DeepEqual(msg.ToolCalls[0], want) || string(msg.Content) != "null" {
				t.Fatalf("wrong tool-only wire body: %s", raw)
			}
		})
	}
}
