package server

import (
	"bytes"
	"encoding/json"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ttftTiming is the rc.Timings key of a streamed request's time to first
// token: from the start of the upstream call that served the stream to the
// first chunk of model output it sent. The otel plugin exports it as
// gen_ai.server.time_to_first_token, in milliseconds. A request that did not
// stream model output (a non-stream call, a cache hit, a refusal, a stream
// that failed before output) has none.
const ttftTiming = "ttft"

// recordTTFT records d as the request's time to first token. It is called on
// the handler's goroutine before the request's post-plugins run: the
// post-parallel plugins read rc.Timings without the lock.
func recordTTFT(rc *models.RequestContext, d time.Duration) {
	if rc == nil || d < 0 {
		return
	}
	rc.RecordTiming(ttftTiming, d)
}

// ttftClock times the upstream call that serves a stream to its first model
// output, and records that time once.
type ttftClock struct {
	started  time.Time
	recorded bool
}

// begin marks the start of the upstream call, immediately before it is made.
func (c *ttftClock) begin() {
	c.started = time.Now()
}

// record records the time since begin as rc's time to first token.
func (c *ttftClock) record(rc *models.RequestContext) {
	c.recordAt(rc, time.Now())
}

// recordAt records at's distance from begin as rc's time to first token: the
// first time only, and never for a call that did not begin.
func (c *ttftClock) recordAt(rc *models.RequestContext, at time.Time) {
	if c == nil || c.recorded || c.started.IsZero() {
		return
	}
	c.recorded = true
	recordTTFT(rc, at.Sub(c.started))
}

// chunkCarriesOutput reports whether a canonical chunk is model output, by
// the test a stream starts on (waitForManagedStreamStart): text, reasoning,
// a tool call, an image, or a finish that is not an error (a completed empty
// answer). Role-only, usage-only and other metadata chunks are not.
func chunkCarriesOutput(chunk models.StreamChunk) bool {
	for _, c := range chunk.Choices {
		d := c.Delta
		if (d.Content != nil && *d.Content != "") || (d.ReasoningContent != nil && *d.ReasoningContent != "") {
			return true
		}
		for _, tc := range d.ToolCalls {
			if tc.Function != nil && (tc.Function.Name != "" || tc.Function.Arguments != "") {
				return true
			}
		}
		if len(d.Images) != 0 {
			var images []json.RawMessage
			if json.Unmarshal(d.Images, &images) == nil && len(images) > 0 {
				return true
			}
		}
		if c.FinishReason != nil && *c.FinishReason != "" && *c.FinishReason != "error" {
			return true
		}
	}
	return false
}

// firstTokenWatch times a stream the gateway relays as the provider sent it
// (Messages, GenAI and Responses passthrough) to its first model output. It
// reads the relayed bytes as server-sent events until isToken accepts a data
// line, records the time to first token then, and reads nothing after.
type firstTokenWatch struct {
	ttftClock
	rc      *models.RequestContext
	isToken func(data []byte) bool
	line    []byte // the current line, until its newline arrives
	skip    bool   // the current line passed maxSSELineBytes
}

func newFirstTokenWatch(rc *models.RequestContext, isToken func(data []byte) bool) *firstTokenWatch {
	return &firstTokenWatch{rc: rc, isToken: isToken}
}

// Write takes the next bytes of the relayed stream, which split lines
// anywhere. It does nothing before begin or after the first token.
func (w *firstTokenWatch) Write(p []byte) {
	if w == nil || w.recorded || w.started.IsZero() {
		return
	}
	for len(p) > 0 && !w.recorded {
		end := bytes.IndexByte(p, '\n')
		if end < 0 {
			w.hold(p)
			return
		}
		w.hold(p[:end])
		if !w.skip {
			w.readLine()
		}
		w.line, w.skip = w.line[:0], false
		p = p[end+1:]
	}
	if w.recorded {
		w.line = nil
	}
}

func (w *firstTokenWatch) hold(p []byte) {
	if w.skip {
		return
	}
	if len(w.line)+len(p) > maxSSELineBytes {
		w.line, w.skip = w.line[:0], true
		return
	}
	w.line = append(w.line, p...)
}

func (w *firstTokenWatch) readLine() {
	data, ok := bytes.CutPrefix(bytes.TrimSuffix(w.line, []byte("\r")), []byte("data:"))
	if !ok {
		return
	}
	if w.isToken(bytes.TrimPrefix(data, []byte(" "))) {
		w.record(w.rc)
	}
}

// anthropicFirstToken reports whether a Messages API stream event is model
// output: a content block delta (text, thinking or tool input), or the
// message's stop reason when the answer finished without one.
func anthropicFirstToken(data []byte) bool {
	if !bytes.Contains(data, []byte("content_block_delta")) && !bytes.Contains(data, []byte("message_delta")) {
		return false
	}
	var ev struct {
		Type  string `json:"type"`
		Delta struct {
			StopReason string `json:"stop_reason"`
		} `json:"delta"`
	}
	if json.Unmarshal(data, &ev) != nil {
		return false
	}
	return ev.Type == "content_block_delta" || (ev.Type == "message_delta" && ev.Delta.StopReason != "")
}

// genaiFirstToken reports whether a streamGenerateContent event is model
// output: a candidate part with text (a thought's included), any other
// content part (a function call, inline data, code), or a candidate's finish
// reason when the answer finished without one. A usage-only event is not.
func genaiFirstToken(data []byte) bool {
	var ev struct {
		Candidates []struct {
			Content struct {
				Parts []map[string]json.RawMessage `json:"parts"`
			} `json:"content"`
			FinishReason string `json:"finishReason"`
		} `json:"candidates"`
	}
	if json.Unmarshal(data, &ev) != nil {
		return false
	}
	for _, candidate := range ev.Candidates {
		if candidate.FinishReason != "" {
			return true
		}
		for _, part := range candidate.Content.Parts {
			for key, raw := range part {
				switch key {
				case "text":
					var text string
					if json.Unmarshal(raw, &text) == nil && text != "" {
						return true
					}
				case "thought", "thoughtSignature", "thought_signature":
					// Flags on a part, not output of their own.
				default:
					return true
				}
			}
		}
	}
	return false
}

// responsesFirstToken reports whether a Responses API stream event is model
// output: any non-empty delta (output text, reasoning, refusal, function
// call arguments, audio), or the response's end when it sent no delta.
func responsesFirstToken(data []byte) bool {
	var ev struct {
		Type  string          `json:"type"`
		Delta json.RawMessage `json:"delta"`
	}
	if json.Unmarshal(data, &ev) != nil {
		return false
	}
	switch {
	case strings.HasSuffix(ev.Type, ".delta"):
		delta := string(bytes.TrimSpace(ev.Delta))
		return delta != "" && delta != `""` && delta != "null"
	case ev.Type == "response.completed" || ev.Type == "response.incomplete":
		return true
	}
	return false
}
