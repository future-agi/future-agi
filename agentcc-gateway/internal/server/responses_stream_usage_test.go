package server

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// The counter reads a stream's deltas whatever the reads cut: one byte at a
// time, CRLF line ends, a space after the colon. Only a string delta counts;
// the last event, a non-string delta and [DONE] count nothing.
func TestResponsesStreamOutputCountsTheDeltasAcrossReads(t *testing.T) {
	stream := "event: response.created\r\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"r\",\"model\":\"gpt-4o\"}}\r\n\r\n" +
		"event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"hello \\\"you\\\"\"}\n\n" +
		"event: response.function_call_arguments.delta\r\ndata: {\"type\": \"response.function_call_arguments.delta\", \"delta\": \"{\\\"q\\\":1}\"}\r\n\r\n" +
		"event: response.something.delta\ndata: {\"type\":\"response.something.delta\",\"delta\":{\"text\":\"not a string\"}}\n\n" +
		"event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"model\":\"gpt-4o\"," +
		"\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"hello\"}]}]," +
		"\"usage\":{\"input_tokens\":12,\"output_tokens\":34,\"total_tokens\":46}}}\n\n" +
		"data: [DONE]\n\n"
	want := len(`hello "you"`) + len(`{"q":1}`)

	var whole responsesStreamOutput
	whole.Write([]byte(stream))
	var bytewise responsesStreamOutput
	for i := 0; i < len(stream); i++ {
		bytewise.Write([]byte{stream[i]})
	}
	if whole.chars != want || bytewise.chars != want {
		t.Fatalf("counted %d characters in one read and %d a byte at a time, want %d", whole.chars, bytewise.chars, want)
	}
}

// A line past the bound is skipped, never held, and the next line still
// counts.
func TestResponsesStreamOutputSkipsALineOverTheBound(t *testing.T) {
	var o responsesStreamOutput
	o.Write([]byte("data: {\"type\":\"response.output_text.delta\",\"delta\":\""))
	for i := 0; i < 3; i++ {
		o.Write([]byte(strings.Repeat("x", maxResponsesDeltaLineBytes/2)))
	}
	if len(o.line) != 0 || !o.skip {
		t.Fatalf("an oversized line is held (%d bytes, skip %v)", len(o.line), o.skip)
	}
	o.Write([]byte("\"}\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"four\"}\n"))
	if o.chars != 4 || len(o.line) != 0 || o.skip {
		t.Fatalf("after an oversized line: %d characters, %d bytes held, skip %v; want 4, 0, false", o.chars, len(o.line), o.skip)
	}
}

// The estimate's prompt is the hold's (billing.EstimateInputTokens over the
// body read in the chat shape, instructions included), read even after the
// client went away; its output is the deltas' text at about 4 characters a
// token. With no body to read, the prompt is 0 and the output still counts.
func TestEstimatedResponsesStreamUsage(t *testing.T) {
	body := `{"model":"gpt-4o","instructions":"` + strings.Repeat("b", 200) + `","input":"` + strings.Repeat("a", 398) + `","stream":true}`
	req, _ := requestview.Responses([]byte(body)).ChatRequest(context.Background())
	wantPrompt := billing.EstimateInputTokens(req)
	if wantPrompt < 150 {
		t.Fatalf("set-up: the hold estimates %d prompt tokens for 598 characters", wantPrompt)
	}
	gone, cancel := context.WithCancel(context.Background())
	cancel()

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.PassThrough = requestview.Responses([]byte(body))
	u := estimatedResponsesStreamUsage(gone, rc, 801)
	if u.PromptTokens != wantPrompt || u.CompletionTokens != 201 || u.TotalTokens != wantPrompt+201 {
		t.Fatalf("estimate %+v, want %d prompt (the hold's) and 201 completion (801 characters)", u, wantPrompt)
	}

	bare := models.AcquireRequestContext()
	defer bare.Release()
	if u := estimatedResponsesStreamUsage(context.Background(), bare, 8); u.PromptTokens != 0 || u.CompletionTokens != 2 || u.TotalTokens != 2 {
		t.Fatalf("with no body: %+v, want 0 prompt and 2 completion", u)
	}
}

// estimateBudgetedResponsesStream puts the estimate on rc.Response, as the
// cost plugin reads it, and marks the run usage_estimated.
func TestEstimateBudgetedResponsesStreamMarksTheRun(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o"}
	estimateBudgetedResponsesStream(context.Background(), rc, &responsesStreamOutput{chars: 40})
	if rc.Response.Usage == nil || rc.Response.Usage.CompletionTokens != 10 || rc.Response.Model != "gpt-4o" {
		t.Fatalf("response %+v, usage %+v", rc.Response, rc.Response.Usage)
	}
	if rc.Metadata["usage_estimated"] != "true" || rc.Metadata[usageUnreadableMetadataKey] != "" {
		t.Fatalf("usage_estimated %q, usage_unreadable %q; want true and none",
			rc.Metadata["usage_estimated"], rc.Metadata[usageUnreadableMetadataKey])
	}
}
