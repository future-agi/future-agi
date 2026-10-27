package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
)

func (h *Handlers) setReflexionHeaders(w http.ResponseWriter, rc *models.RequestContext) {
	if rc.Metadata["reflexion_debug"] == "true" {
		w.Header().Set("x-agentcc-reflexion-attempts", rc.Metadata["reflexion_attempts"])
		w.Header().Set("x-agentcc-provider-attempts", rc.Metadata["provider_attempts"])
	}
	if rc.Metadata["usage_incomplete"] == "true" {
		w.Header().Set("x-agentcc-usage-incomplete", "true")
	}
}

func (h *Handlers) chatAttempt(ctx context.Context, rc *models.RequestContext, p providers.Provider) (*models.ChatCompletionResponse, error) {
	if err := h.engine.BeginProviderAttempt(ctx, rc); err != nil {
		return nil, err
	}
	resp, err := p.ChatCompletion(ctx, rc.Request)
	rc.Response = resp
	if resp != nil {
		rc.ResolvedModel = resp.Model
	}
	h.engine.EndProviderAttempt(ctx, rc)
	return resp, err
}

// Bounds both content and framing overhead. At most one candidate is retained;
// retries release the previous frames before opening another upstream stream.
const maxReflexionStreamBytes = 2 << 20
const maxReflexionStreamChunks = 8192

type bufferedChoice struct {
	content   strings.Builder
	reasoning strings.Builder
	finish    string
	tools     map[int]*models.ToolCall
}

type bufferedCandidate struct {
	frames  []models.StreamChunk
	bytes   int
	id      string
	created int64
	model   string
	usage   *models.Usage
	choices map[int]*bufferedChoice
}

func newBufferedCandidate() *bufferedCandidate {
	return &bufferedCandidate{choices: make(map[int]*bufferedChoice)}
}

func (b *bufferedCandidate) add(chunk models.StreamChunk) error {
	// Even a frame that exceeds the body limit may carry measured usage. Keep
	// its scalar counters for settlement, without retaining its rejected body.
	if chunk.Usage != nil {
		u := chunk.Usage
		b.usage = &models.Usage{PromptTokens: u.PromptTokens, CompletionTokens: u.CompletionTokens, TotalTokens: u.TotalTokens}
	}
	// Refuse large payloads before JSON encoding can allocate another full copy.
	if streamChunkFloor(chunk) > maxReflexionStreamBytes-b.bytes || len(b.frames) >= maxReflexionStreamChunks {
		return models.ErrGuardrailBlocked("reflection_buffer_exceeded", "Response exceeds the guarded streaming buffer limit")
	}
	encoded, err := json.Marshal(chunk)
	if err != nil {
		return models.ErrUpstreamProvider(http.StatusBadGateway, "invalid upstream stream chunk")
	}
	if b.bytes+len(encoded) > maxReflexionStreamBytes || len(b.frames) >= maxReflexionStreamChunks {
		return models.ErrGuardrailBlocked("reflection_buffer_exceeded", "Response exceeds the guarded streaming buffer limit")
	}
	// Providers can reuse chunk storage; own the bytes we will validate/replay.
	var owned models.StreamChunk
	if json.Unmarshal(encoded, &owned) != nil {
		return models.ErrUpstreamProvider(http.StatusBadGateway, "invalid upstream stream chunk")
	}
	chunk = owned
	b.bytes += len(encoded)
	b.frames = append(b.frames, chunk)
	if b.id == "" {
		b.id = chunk.ID
	}
	if b.created == 0 {
		b.created = chunk.Created
	}
	if chunk.Model != "" {
		b.model = chunk.Model
	}
	if chunk.Usage != nil {
		b.usage = chunk.Usage
	}
	for _, c := range chunk.Choices {
		if c.Index < 0 {
			return models.ErrUpstreamProvider(http.StatusBadGateway, "invalid stream choice index")
		}
		choice := b.choices[c.Index]
		if choice == nil {
			choice = &bufferedChoice{tools: make(map[int]*models.ToolCall)}
			b.choices[c.Index] = choice
		}
		if c.Delta.Content != nil {
			choice.content.WriteString(*c.Delta.Content)
		}
		if c.Delta.ReasoningContent != nil {
			choice.reasoning.WriteString(*c.Delta.ReasoningContent)
		}
		if c.FinishReason != nil {
			choice.finish = *c.FinishReason
		}
		for _, d := range c.Delta.ToolCalls {
			if d.Index < 0 {
				return models.ErrUpstreamProvider(http.StatusBadGateway, "invalid stream tool index")
			}
			t := choice.tools[d.Index]
			if t == nil {
				t = &models.ToolCall{}
				choice.tools[d.Index] = t
			}
			if d.ID != "" {
				t.ID = d.ID
			}
			if d.Type != "" {
				t.Type = d.Type
			}
			if d.Function != nil {
				t.Function.Name += d.Function.Name
				t.Function.Arguments += d.Function.Arguments
			}
		}
	}
	return nil
}

func streamChunkFloor(c models.StreamChunk) int {
	n := len(c.ID) + len(c.Object) + len(c.Model) + len(c.SystemFingerprint)
	for k, v := range c.Extra {
		n += len(k) + len(v)
	}
	if c.Usage != nil {
		for k, v := range c.Usage.Extra {
			n += len(k) + len(v)
		}
		if c.Usage.PromptTokensDetails != nil {
			n += len(*c.Usage.PromptTokensDetails)
		}
		if c.Usage.CompletionTokensDetails != nil {
			n += len(*c.Usage.CompletionTokensDetails)
		}
	}
	for _, choice := range c.Choices {
		n += 32 + len(choice.Delta.Role)
		if choice.FinishReason != nil {
			n += len(*choice.FinishReason)
		}
		if choice.Delta.Content != nil {
			n += len(*choice.Delta.Content)
		}
		if choice.Delta.ReasoningContent != nil {
			n += len(*choice.Delta.ReasoningContent)
		}
		for _, tool := range choice.Delta.ToolCalls {
			n += 32 + len(tool.ID) + len(tool.Type)
			if tool.Function != nil {
				n += len(tool.Function.Name) + len(tool.Function.Arguments)
			}
		}
	}
	return n
}

func (b *bufferedCandidate) response() *models.ChatCompletionResponse {
	r := &models.ChatCompletionResponse{ID: b.id, Created: b.created, Object: "chat.completion", Model: b.model, Usage: b.usage}
	indices := make([]int, 0, len(b.choices))
	for i := range b.choices {
		indices = append(indices, i)
	}
	sort.Ints(indices)
	for _, i := range indices {
		c := b.choices[i]
		content, _ := json.Marshal(c.content.String())
		m := models.Message{Role: "assistant", Content: content, ReasoningContent: c.reasoning.String()}
		tools := make([]int, 0, len(c.tools))
		for j := range c.tools {
			tools = append(tools, j)
		}
		sort.Ints(tools)
		for _, j := range tools {
			m.ToolCalls = append(m.ToolCalls, *c.tools[j])
		}
		r.Choices = append(r.Choices, models.Choice{Index: i, Message: m, FinishReason: c.finish})
	}
	return r
}

func collectCandidate(ctx context.Context, chunks <-chan models.StreamChunk, errs <-chan error, first *models.StreamChunk) (*bufferedCandidate, error) {
	b := newBufferedCandidate()
	if first != nil {
		if err := b.add(*first); err != nil {
			return b, err
		}
	}
	for {
		if ctx.Err() != nil {
			return b, models.ErrRequestTimeout("request canceled while buffering response")
		}
		select {
		case c, ok := <-chunks:
			if !ok {
				// Providers enqueue errors before closing the data channel. Do not
				// mistake a partial stream with a queued error for successful EOF.
				select {
				case err := <-errs:
					if err != nil {
						return b, err
					}
				default:
				}
				if len(b.choices) == 0 {
					return b, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream stream has no choices")
				}
				for _, c := range b.choices {
					if c.finish == "" {
						return b, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream stream ended without a finish reason")
					}
				}
				return b, nil
			}
			if err := b.add(c); err != nil {
				return b, err
			}
		case err, ok := <-errs:
			if !ok {
				errs = nil
				continue
			}
			if err != nil {
				return b, err
			}
		case <-ctx.Done():
			return b, models.ErrRequestTimeout("request canceled while buffering response")
		}
	}
}

// A synchronous check can redact the validated response. Replaying the original
// upstream frames in that case would undo the redaction and disclose its input.
func (b *bufferedCandidate) replayFrames(response *models.ChatCompletionResponse) ([]models.StreamChunk, error) {
	before, err := json.Marshal(b.response().Choices)
	if err != nil {
		return nil, err
	}
	after, err := json.Marshal(response.Choices)
	if err != nil {
		return nil, err
	}
	if bytes.Equal(before, after) {
		return b.frames, nil
	}
	out := newBufferedCandidate()
	for _, choice := range response.Choices {
		m := choice.Message
		if len(m.ThinkingBlocks) > 0 || len(m.Annotations) > 0 {
			return nil, models.ErrGuardrailBlocked("stream_transform_unsupported", "Response transformation cannot be safely streamed")
		}
		var text string
		if len(m.Content) > 0 && string(m.Content) != "null" {
			if json.Unmarshal(m.Content, &text) != nil {
				return nil, models.ErrGuardrailBlocked("stream_transform_unsupported", "Response transformation cannot be safely streamed")
			}
		}
		delta := models.Delta{Role: "assistant", Content: &text}
		if m.ReasoningContent != "" {
			delta.ReasoningContent = &m.ReasoningContent
		}
		for i, tool := range m.ToolCalls {
			fn := tool.Function
			delta.ToolCalls = append(delta.ToolCalls, models.ToolCallDelta{Index: i, ID: tool.ID, Type: tool.Type, Function: &fn})
		}
		chunk := models.StreamChunk{ID: b.id, Object: "chat.completion.chunk", Created: b.created, Model: response.Model, Choices: []models.StreamChoice{{Index: choice.Index, Delta: delta}}}
		if err := out.add(chunk); err != nil {
			return nil, err
		}
		finish := choice.FinishReason
		chunk.Choices = []models.StreamChoice{{Index: choice.Index, FinishReason: &finish}}
		if err := out.add(chunk); err != nil {
			return nil, err
		}
	}
	return out.frames, nil
}

func (h *Handlers) replayCandidate(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider, candidate *bufferedCandidate) {
	if ctx.Err() != nil {
		models.WriteError(w, models.ErrRequestTimeout("request canceled before response replay"))
		return
	}
	if candidate == nil || rc.Response == nil {
		models.WriteError(w, models.ErrInternal("no validated stream candidate"))
		return
	}
	frames, err := candidate.replayFrames(rc.Response)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	sse := streaming.NewSSEWriter(w)
	if sse == nil {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}
	h.setAgentccHeadersForProvider(w, rc, provider)
	sse.WriteHeaders()
	for _, frame := range frames {
		if ctx.Err() != nil {
			return
		}
		// Usage is emitted exactly once, as the aggregate of every measured
		// provider attempt, not just the passing generation.
		frame.Usage = nil
		if len(frame.Choices) == 0 && len(frame.Extra) == 0 {
			continue
		}
		if err := sse.WriteChunk(frame); err != nil {
			return
		}
	}
	if final := h.buildStreamingMetadataChunk(rc, candidate.id, candidate.created, rc.Response.Usage); final != nil {
		if err := sse.WriteChunk(*final); err != nil {
			return
		}
	}
	sse.WriteDone()
}
