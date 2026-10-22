package server

import (
	"encoding/json"
	"slices"
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
// request log and the trace exporter with no completion text at all, and an
// output check reads a streamed answer from it. It is switched on when an output
// check reads the answer (rc.Flags.AnswerReaders) or a sink is configured to
// record bodies — otherwise the assembly is pure cost.
//
// Content deltas only. Tool-call deltas are not assembled; a partial arguments
// string is worse than an absent one.
//
// Each choice (each candidate, on a Gemini stream) is assembled on its own, so
// an output check reads each choice's text as the client gets it.
type streamCapture struct {
	choices   []*capturedChoice // in the order first seen
	size      int               // bytes of text held, across choices
	truncated bool
}

type capturedChoice struct {
	index        int
	content      strings.Builder
	finishReason string
}

// maxStreamCaptureChoices bounds how many choices a capture holds. A choice
// past it is not assembled, and the capture counts as truncated.
const maxStreamCaptureChoices = 128

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
		hasContent := choice.Delta.Content != nil && *choice.Delta.Content != ""
		hasFinish := choice.FinishReason != nil && *choice.FinishReason != ""
		if !hasContent && !hasFinish {
			continue
		}
		cc := c.choice(choice.Index)
		if cc == nil {
			c.truncated = true
			continue
		}
		if hasContent && !c.truncated {
			if c.size+len(*choice.Delta.Content) > maxStreamCaptureBytes {
				c.truncated = true
			} else {
				cc.content.WriteString(*choice.Delta.Content)
				c.size += len(*choice.Delta.Content)
			}
		}
		if hasFinish {
			cc.finishReason = *choice.FinishReason
		}
	}
}

// choice is the assembly of the choice at index, or nil when the capture
// already holds maxStreamCaptureChoices others.
func (c *streamCapture) choice(index int) *capturedChoice {
	for _, cc := range c.choices {
		if cc.index == index {
			return cc
		}
	}
	if len(c.choices) >= maxStreamCaptureChoices {
		return nil
	}
	cc := &capturedChoice{index: index}
	c.choices = append(c.choices, cc)
	return cc
}

// hasText reports whether the stream sent any text.
func (c *streamCapture) hasText() bool {
	return c != nil && c.size > 0
}

// applyTo fills in the response choices from what was captured, one per
// choice the stream sent, in index order. Called from finalizeStream, before
// post-plugins run, so writing to rc is still legal.
func (c *streamCapture) applyTo(resp *models.ChatCompletionResponse) {
	if c == nil || resp == nil || len(c.choices) == 0 {
		return
	}
	sorted := slices.Clone(c.choices)
	slices.SortFunc(sorted, func(a, b *capturedChoice) int { return a.index - b.index })
	choices := make([]models.Choice, 0, len(sorted))
	for _, cc := range sorted {
		// Message.Content is raw JSON, so the assembled text is marshalled as
		// a JSON string rather than assigned directly.
		content, err := json.Marshal(cc.content.String())
		if err != nil {
			continue
		}
		choices = append(choices, models.Choice{
			Index:        cc.index,
			Message:      models.Message{Role: "assistant", Content: content},
			FinishReason: cc.finishReason,
		})
	}
	resp.Choices = choices
}
