package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// responsesStreamWith is a Responses API stream that ends with final, an
// event line that carries the response object.
func responsesStreamWith(final string) string {
	return "event: response.created\n" +
		`data: {"type":"response.created","sequence_number":0,"response":{"id":"r1","status":"in_progress","model":"gpt-4o","output":[],"usage":null}}` + "\n\n" +
		"event: response.output_text.delta\n" +
		`data: {"type":"response.output_text.delta","sequence_number":1,"delta":"the text says response.completed"}` + "\n\n" +
		final + "\n\n"
}

const completedEvent = "event: response.completed\n" +
	`data: {"type":"response.completed","sequence_number":2,"response":{"id":"r1","status":"completed","model":"gpt-4o-2024-08-06",` +
	`"output":[{"type":"message","content":[{"type":"output_text","text":"hi"}]}],` +
	`"usage":{"input_tokens":1000,"output_tokens":500,"total_tokens":1500}}}`

// The stream's usage is the one its last event reports, wherever the bytes
// the gateway relays split it, and the model the event names becomes the
// resolved model.
func TestResponsesStreamUsage_ReadsTheLastEventAtAnySplit(t *testing.T) {
	stream := responsesStreamWith(completedEvent)
	for i := 0; i <= len(stream); i++ {
		var u responsesStreamUsage
		u.Write([]byte(stream[:i]))
		u.Write([]byte(stream[i:]))
		rc := &models.RequestContext{Metadata: map[string]string{}}
		if !u.applyTo(rc) {
			t.Fatalf("split at %d: the usage was not read", i)
		}
		got := rc.Response.Usage
		if got.PromptTokens != 1000 || got.CompletionTokens != 500 || got.TotalTokens != 1500 {
			t.Fatalf("split at %d: usage %+v, want 1000 in, 500 out, 1500 total", i, got)
		}
		if rc.ResolvedModel != "gpt-4o-2024-08-06" || rc.Response.Model != "gpt-4o-2024-08-06" {
			t.Fatalf("split at %d: resolved model %q, response model %q; want the last event's", i, rc.ResolvedModel, rc.Response.Model)
		}
	}
}

// A response that stopped early ends its stream with response.incomplete or
// response.failed, which carry its usage too.
func TestResponsesStreamUsage_AResponseThatStoppedEarlyIsPriced(t *testing.T) {
	for _, ev := range []string{"response.incomplete", "response.failed"} {
		var u responsesStreamUsage
		u.Write([]byte(responsesStreamWith("event: " + ev + "\n" +
			`data: {"type":"` + ev + `","response":{"id":"r1","model":"gpt-4o","output":[],"usage":{"input_tokens":7,"output_tokens":3,"total_tokens":10}}}`)))
		rc := &models.RequestContext{Metadata: map[string]string{}}
		if !u.applyTo(rc) || rc.Response.Usage.TotalTokens != 10 {
			t.Errorf("%s: usage %+v, want the event's 10 tokens", ev, rc.Response.Usage)
		}
	}
}

// The OpenAI-run tools the response used are priced on top of its tokens, as
// for a response that is not streamed.
func TestResponsesStreamUsage_CountsTheServerToolsTheResponseUsed(t *testing.T) {
	var u responsesStreamUsage
	u.Write([]byte(responsesStreamWith("event: response.completed\n" +
		`data: {"type":"response.completed","response":{"model":"gpt-4o","tools":[{"type":"web_search"}],` +
		`"output":[{"type":"web_search_call","id":"ws_1"},{"type":"message","content":[]}],` +
		`"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}`)))
	rc := &models.RequestContext{Metadata: map[string]string{}}
	u.applyTo(rc)
	if st := rc.Response.Usage.ServerToolUse; st == nil || st.WebSearchRequests != 1 {
		t.Errorf("server tool use %+v, want 1 web search", st)
	}
}

// A stream with no event that ends the response (cut off, or a background
// response that goes on after it) has no usage to price: rc.Response keeps
// the model and no usage, so the cost plugin prices nothing rather than
// zero, and an over-long last line is skipped, not held.
func TestResponsesStreamUsage_NoFinalEventMeansNoUsage(t *testing.T) {
	for name, stream := range map[string]string{
		"cut off":        responsesStreamWith(""),
		"over the limit": responsesStreamWith("data: " + `{"type":"response.completed","response":{"model":"gpt-4o","output":"` + strings.Repeat("x", maxResponsesUsageLineBytes) + `","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}`),
	} {
		var u responsesStreamUsage
		u.Write([]byte(stream))
		rc := &models.RequestContext{Metadata: map[string]string{}, ResolvedModel: "gpt-4o"}
		if u.applyTo(rc) || rc.Response == nil || rc.Response.Usage != nil || rc.Response.Model != "gpt-4o" {
			t.Errorf("%s: applyTo read a usage, or left rc.Response %+v; want the model and no usage", name, rc.Response)
		}
		if want := name == "over the limit"; u.skipped != want {
			t.Errorf("%s: skipped = %v, want %v", name, u.skipped, want)
		}
		if len(u.line) > maxResponsesUsageLineBytes {
			t.Errorf("%s: held a %d-byte line", name, len(u.line))
		}
	}
}

// The reader says when the line it could not read was a last event
// (finalSkipped): a data line over the limit that names response.completed,
// response.incomplete or response.failed in its first bytes, or that follows
// an "event:" line naming one. A long line of another event (an output item)
// is not, so a stream cut off after one is not taken for a last event the
// gateway could not read.
func TestResponsesStreamUsage_NamesALastEventOverTheLimit(t *testing.T) {
	big := strings.Repeat("x", maxResponsesUsageLineBytes)
	for _, tc := range []struct {
		name, stream string
		final        bool
	}{
		{"data line naming the last event", responsesStreamWith(`data: {"type":"response.completed","response":{"output":"` + big + `"}}`), true},
		{"after an event line naming it", "event: response.incomplete\n" + `data: {"response":{"output":"` + big + `"},"type":"response.incomplete"}` + "\n\n", true},
		{"another event's long line, then cut off", "event: response.output_item.done\n" + `data: {"type":"response.output_item.done","item":{"output":"` + big + `"}}` + "\n\n", false},
		{"cut off", responsesStreamWith(""), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, split := range []int{1, 7, 4096, len(tc.stream)} {
				var u responsesStreamUsage
				for p := tc.stream; len(p) > 0; {
					n := min(split, len(p))
					u.Write([]byte(p[:n]))
					p = p[n:]
				}
				if u.finalSkipped != tc.final {
					t.Errorf("split %d: finalSkipped = %v, want %v", split, u.finalSkipped, tc.final)
				}
			}
		})
	}
}
