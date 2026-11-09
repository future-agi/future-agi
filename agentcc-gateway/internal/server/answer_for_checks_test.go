package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func assembledText(t *testing.T, a *sseAnswer) string {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	a.giveTo(rc)
	if rc.Response == nil || len(rc.Response.Choices) == 0 {
		return ""
	}
	var parts []string
	for _, c := range rc.Response.Choices {
		var s string
		if err := json.Unmarshal(c.Message.Content, &s); err != nil {
			t.Fatalf("choice content is not a JSON string: %s", c.Message.Content)
		}
		parts = append(parts, s)
	}
	return strings.Join(parts, "|")
}

const anthropicStream = "event: message_start\r\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"m\",\"usage\":{\"input_tokens\":5}}}\r\n\r\n" +
	"event: content_block_delta\r\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"You can run DROP \"}}\r\n\r\n" +
	"event: content_block_delta\r\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{}\"}}\r\n\r\n" +
	"event: content_block_delta\r\ndata:{\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"TABLE users;\"}}\r\n\r\n" +
	"event: message_delta\r\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":9}}\r\n\r\n"

// The relayed bytes split lines anywhere: one byte at a time, CRLF line ends
// and a data field with no space after the colon still assemble the text.
// Tool input deltas are not text.
func TestSSEAnswer_AssemblesTextAcrossAnySplit(t *testing.T) {
	for _, size := range []int{1, 7, len(anthropicStream)} {
		a := newSSEAnswer(true, anthropicStreamDelta)
		for i := 0; i < len(anthropicStream); i += size {
			a.Write([]byte(anthropicStream[i:min(i+size, len(anthropicStream))]))
		}
		if got := assembledText(t, a); got != "You can run DROP TABLE users;" {
			t.Errorf("writes of %d bytes assembled %q", size, got)
		}
		if a.capture.choices[0].finishReason != "stop" {
			t.Errorf("finish reason %q, want stop (end_turn)", a.capture.choices[0].finishReason)
		}
	}
}

// A line longer than maxSSELineBytes is skipped, and the lines after it are
// still read.
func TestSSEAnswer_SkipsAnOverlongLine(t *testing.T) {
	a := newSSEAnswer(true, anthropicStreamDelta)
	a.Write([]byte("data: " + strings.Repeat("x", maxSSELineBytes) + "\n"))
	a.Write([]byte(`data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"after"}}` + "\n"))
	if got := assembledText(t, a); got != "after" {
		t.Errorf("assembled %q, want the line after the overlong one", got)
	}
}

// Disabled, it costs nothing and gives the checks nothing.
func TestSSEAnswer_DisabledIsInert(t *testing.T) {
	a := newSSEAnswer(false, anthropicStreamDelta)
	a.Write([]byte(anthropicStream))
	rc := models.AcquireRequestContext()
	defer rc.Release()
	a.giveTo(rc)
	if rc.Response != nil {
		t.Errorf("a disabled assembly set a response: %+v", rc.Response)
	}
}

// Each Gemini candidate's text lands at its own index; thought summaries are
// not text.
func TestGenAIStreamDelta_KeepsCandidatesApart(t *testing.T) {
	a := newSSEAnswer(true, genaiStreamDelta)
	a.Write([]byte(`data: {"candidates":[{"content":{"parts":[{"text":"thinking","thought":true},{"text":"B1 "}]},"index":1},{"content":{"parts":[{"text":"A1 "}]},"index":0}]}` + "\n\n"))
	a.Write([]byte(`data: {"candidates":[{"content":{"parts":[{"text":"A2"}]},"index":0}]}` + "\n\n"))
	a.Write([]byte(`data: {"candidates":[{"content":{"parts":[{"text":"B2"}]},"finishReason":"MAX_TOKENS","index":1}]}` + "\n\n"))
	if got := assembledText(t, a); got != "A1 A2|B1 B2" {
		t.Errorf("assembled %q, want each candidate on its own", got)
	}
}

// A Responses stream assembles its output_text deltas; one that sent none
// gives the completed response's text instead.
func TestResponsesStreamDecoder(t *testing.T) {
	for _, tc := range []struct{ name, stream, want string }{
		{"deltas", "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"DROP \"}\n\n" +
			"event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"TABLE\"}\n\n" +
			"event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"DROP TABLE\"}]}]}}\n\n",
			"DROP TABLE"},
		{"completed only", "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"DROP TABLE\"}]}]}}\n\n",
			"DROP TABLE"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := newSSEAnswer(true, responsesStreamDecoder())
			a.Write([]byte(tc.stream))
			if got := assembledText(t, a); got != tc.want {
				t.Errorf("assembled %q, want %q", got, tc.want)
			}
		})
	}
}
