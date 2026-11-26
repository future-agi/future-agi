package server

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// maxStreamCaptureBytes bounds the assembled completion. Past it the tail is
// dropped rather than growing without limit on a runaway generation.
const maxStreamCaptureBytes = 2 << 20

// streamCapture reassembles a streamed completion so post-plugins can see what
// was actually returned.
//
// Nothing else in the gateway keeps this: finalizeStream builds rc.Response
// from the usage totals alone, so without this a streamed request reaches the
// request log and the trace exporter with no completion text at all. Only
// switched on when a sink is configured to record bodies — otherwise the
// assembly is pure cost.
//
// Content deltas only. Tool-call deltas are not assembled; a partial arguments
// string is worse than an absent one.
type streamCapture struct {
	content      strings.Builder
	finishReason string
	truncated    bool
}

func newStreamCapture(enabled bool) *streamCapture {
	if !enabled {
		return nil
	}
	return &streamCapture{}
}

// observe accumulates one chunk. Safe on a nil receiver so call sites stay
// free of enabled-checks.
func (c *streamCapture) observe(chunk models.StreamChunk) {
	if c == nil {
		return
	}
	for _, choice := range chunk.Choices {
		if choice.Delta.Content != nil && !c.truncated {
			if c.content.Len()+len(*choice.Delta.Content) > maxStreamCaptureBytes {
				c.truncated = true
			} else {
				c.content.WriteString(*choice.Delta.Content)
			}
		}
		if choice.FinishReason != nil && *choice.FinishReason != "" {
			c.finishReason = *choice.FinishReason
		}
	}
}

// applyTo fills in the response choices from what was captured. Called from
// finalizeStream, before post-plugins run, so writing to rc is still legal.
func (c *streamCapture) applyTo(resp *models.ChatCompletionResponse) {
	if c == nil || resp == nil {
		return
	}
	if c.content.Len() == 0 && c.finishReason == "" {
		return
	}
	// Message.Content is raw JSON, so the assembled text is marshalled as a
	// JSON string rather than assigned directly.
	content, err := json.Marshal(c.content.String())
	if err != nil {
		return
	}
	resp.Choices = []models.Choice{{
		Index:        0,
		Message:      models.Message{Role: "assistant", Content: content},
		FinishReason: c.finishReason,
	}}
}
