package server

import (
	"cmp"
	"encoding/json"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// All retained payload strings share one byte budget, including tool IDs,
// names, arguments, refusal text and encoded audio. Cardinality is bounded too.
const maxStreamCaptureBytes = 2 << 20
const maxStreamCaptureChoices = 128
const maxStreamCaptureTools = 128

// streamCapture reconstructs the response for post-plugins and full-body logs.
// It is enabled only for an answer reader or a sink that records bodies.
// The client still receives the original deltas, never this reconstruction.
type streamCapture struct {
	choices   []*capturedChoice
	size      int
	truncated bool
	// holdAll uses the held stream's larger, still finite byte budget.
	limit int
}

type capturedChoice struct {
	index        int
	content      strings.Builder
	finishReason string
	tools        []*capturedTool
	refusal      strings.Builder
	hasRefusal   bool
	audio        *models.MessageAudio
	audioData    strings.Builder
	transcript   strings.Builder
}

type capturedTool struct {
	index     int
	id        string
	kind      string
	name      strings.Builder
	arguments strings.Builder
}

func newStreamCapture(enabled bool) *streamCapture {
	if !enabled {
		return nil
	}
	return &streamCapture{}
}

// observe is safe on nil so disabled capture costs no assembly.
func (c *streamCapture) observe(chunk models.StreamChunk) {
	if c == nil {
		return
	}
	for _, choice := range chunk.Choices {
		d := choice.Delta
		hasContent := d.Content != nil && *d.Content != ""
		hasFinish := choice.FinishReason != nil && *choice.FinishReason != ""
		if !hasContent && !hasFinish && len(d.ToolCalls) == 0 && d.Refusal == nil && d.Audio == nil {
			continue
		}
		cc := c.choice(choice.Index)
		if cc == nil {
			c.truncated = true
			continue
		}
		if hasContent {
			c.append(&cc.content, *d.Content)
		}
		for _, delta := range d.ToolCalls {
			c.observeTool(cc, delta)
		}
		if d.Refusal != nil {
			cc.hasRefusal = true
			c.append(&cc.refusal, *d.Refusal)
		}
		if d.Audio != nil {
			if cc.audio == nil {
				cc.audio = &models.MessageAudio{}
			}
			c.identity(&cc.audio.ID, d.Audio.ID)
			if d.Audio.ExpiresAt != 0 {
				cc.audio.ExpiresAt = d.Audio.ExpiresAt
			}
			c.append(&cc.audioData, d.Audio.Data)
			c.append(&cc.transcript, d.Audio.Transcript)
		}
		// Finish reasons are recorded even after the payload reached its bound.
		if hasFinish {
			cc.finishReason = *choice.FinishReason
		}
	}
}

func (c *streamCapture) append(dst *strings.Builder, fragment string) {
	if fragment == "" || c.truncated {
		return
	}
	if len(fragment) > c.maxBytes()-c.size {
		c.truncated = true
		return
	}
	dst.WriteString(fragment)
	c.size += len(fragment)
}

// IDs and types are identities, not text fragments. Repeated identities are
// harmless; a changed identity makes the capture incomplete, not a new call.
func (c *streamCapture) identity(dst *string, value string) {
	if value == "" || value == *dst || c.truncated {
		return
	}
	if *dst != "" || len(value) > c.maxBytes()-c.size {
		c.truncated = true
		return
	}
	*dst = value
	c.size += len(value)
}

func (c *streamCapture) observeTool(cc *capturedChoice, d models.ToolCallDelta) {
	if c.truncated {
		return
	}
	var tool *capturedTool
	for _, candidate := range cc.tools {
		if candidate.index == d.Index {
			tool = candidate
			break
		}
	}
	if tool == nil {
		if d.Index < 0 || len(cc.tools) >= maxStreamCaptureTools {
			c.truncated = true
			return
		}
		tool = &capturedTool{index: d.Index}
		cc.tools = append(cc.tools, tool)
	}
	c.identity(&tool.id, d.ID)
	c.identity(&tool.kind, d.Type)
	if d.Function != nil {
		c.append(&tool.name, d.Function.Name)
		c.append(&tool.arguments, d.Function.Arguments)
	}
}

func (c *streamCapture) choice(index int) *capturedChoice {
	for _, cc := range c.choices {
		if cc.index == index {
			return cc
		}
	}
	if index < 0 || len(c.choices) >= maxStreamCaptureChoices {
		return nil
	}
	cc := &capturedChoice{index: index}
	c.choices = append(c.choices, cc)
	return cc
}

func (c *streamCapture) maxBytes() int {
	if c.limit > 0 {
		return c.limit
	}
	return maxStreamCaptureBytes
}

func (c *streamCapture) holdAll() {
	if c != nil {
		c.limit = maxHeldStreamBytes
	}
}

// complete reports whether the existing text-only checks can read the whole
// held answer. This differs from a faithful log capture of non-text output.
func (c *streamCapture) complete() bool {
	if c == nil {
		return true
	}
	if c.truncated {
		return false
	}
	for _, cc := range c.choices {
		if !cc.toolsComplete() {
			return false
		}
		// The checks read a refusal and a transcript as text; audio with no
		// transcript they cannot read, and a held stream fails closed
		// rather than release it unchecked.
		if cc.audio != nil && cc.transcript.Len() == 0 {
			return false
		}
	}
	return true
}

func (cc *capturedChoice) toolsComplete() bool {
	if len(cc.tools) == 0 {
		return true
	}
	if cc.finishReason == "" || cc.finishReason == "length" || cc.finishReason == "content_filter" {
		return false
	}
	for _, tool := range cc.tools {
		if tool.id == "" || tool.kind != "function" || tool.name.Len() == 0 {
			return false
		}
	}
	return true
}

func (c *streamCapture) hasText() bool {
	if c != nil {
		for _, cc := range c.choices {
			if cc.content.Len() > 0 {
				return true
			}
		}
	}
	return false
}

// applyTo runs before post-plugins. Partial captures carry an explicit marker
// in the stored body, so a disconnected or bounded stream is never represented
// as a complete successful empty answer. Partial tool calls retain the fields
// that arrived, even when the stream ended before its finish reason.
func (c *streamCapture) applyTo(resp *models.ChatCompletionResponse) {
	if c == nil || resp == nil {
		return
	}
	incomplete := c.truncated
	sorted := slices.Clone(c.choices)
	slices.SortFunc(sorted, func(a, b *capturedChoice) int { return cmp.Compare(a.index, b.index) })
	var choices []models.Choice
	for _, cc := range sorted {
		if cc.finishReason == "" || !cc.toolsComplete() {
			incomplete = true
		}
		if cc.content.Len() == 0 && cc.finishReason == "" && len(cc.tools) == 0 && !cc.hasRefusal && cc.audio == nil {
			continue
		}
		msg := models.Message{Role: "assistant"}
		if cc.content.Len() > 0 || (len(cc.tools) == 0 && !cc.hasRefusal && cc.audio == nil) {
			msg.Content, _ = json.Marshal(cc.content.String())
		}
		if cc.hasRefusal {
			refusal := cc.refusal.String()
			msg.Refusal = &refusal
		}
		if cc.audio != nil {
			audio := *cc.audio
			audio.Data = cc.audioData.String()
			audio.Transcript = cc.transcript.String()
			msg.Audio = &audio
		}
		tools := slices.Clone(cc.tools)
		slices.SortFunc(tools, func(a, b *capturedTool) int { return cmp.Compare(a.index, b.index) })
		for _, tool := range tools {
			msg.ToolCalls = append(msg.ToolCalls, models.ToolCall{ID: tool.id, Type: tool.kind, Function: models.FunctionCall{Name: tool.name.String(), Arguments: tool.arguments.String()}})
		}
		choices = append(choices, models.Choice{Index: cc.index, Message: msg, FinishReason: cc.finishReason})
	}
	if len(choices) > 0 {
		resp.Choices = choices
	}
	if incomplete {
		if resp.Extra == nil {
			resp.Extra = make(map[string]json.RawMessage)
		}
		resp.Extra["agentcc_capture_incomplete"] = json.RawMessage(`true`)
	}
}
