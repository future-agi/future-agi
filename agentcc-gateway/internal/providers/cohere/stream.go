package cohere

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// streamState tracks state across Cohere stream events.
type streamState struct {
	model   string
	chunkID string
	created int64

	// toolCallIndex tracks the current tool call index for streaming tool calls.
	toolCallIndex int
}

func newStreamState(model string) *streamState {
	now := time.Now()
	return &streamState{
		model:   model,
		chunkID: fmt.Sprintf("cohere-%d", now.UnixNano()),
		created: now.Unix(),
	}
}

// Cohere stream event envelope.
type streamEvent struct {
	Type  string          `json:"type"`
	Delta json.RawMessage `json:"delta,omitempty"`

	// message-start
	ID string `json:"id,omitempty"`

	// message-end
	FinishReason string       `json:"finish_reason,omitempty"`
	Usage        *cohereUsage `json:"usage,omitempty"`

	// tool-call-start: index of the tool call in the response
	Index int `json:"index,omitempty"`
}

type contentDelta struct {
	Message struct {
		Content struct {
			Text string `json:"text"`
		} `json:"content"`
	} `json:"message"`
}

// toolCallStartDelta is the delta payload for tool-call-start events.
type toolCallStartDelta struct {
	Message struct {
		ToolCalls struct {
			ID       string `json:"id"`
			Type     string `json:"type"`
			Function struct {
				Name      string `json:"name"`
				Arguments string `json:"arguments"`
			} `json:"function"`
		} `json:"tool_calls"`
	} `json:"message"`
}

// toolCallDeltaPayload is the delta payload for tool-call-delta events.
type toolCallDeltaPayload struct {
	Message struct {
		ToolCalls struct {
			Function struct {
				Arguments string `json:"arguments"`
			} `json:"function"`
		} `json:"tool_calls"`
	} `json:"message"`
}

// parseStreamData parses a Cohere SSE data payload and returns a StreamChunk.
// Returns (chunk, done, error).
func (s *streamState) parseStreamData(data string) (*models.StreamChunk, bool, error) {
	var event streamEvent
	if err := json.Unmarshal([]byte(data), &event); err != nil {
		return nil, false, nil // Skip unparseable.
	}

	switch event.Type {
	case "message-start":
		if event.ID != "" {
			s.chunkID = event.ID
		}
		role := "assistant"
		return &models.StreamChunk{
			ID:      s.chunkID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{
					Index: 0,
					Delta: models.Delta{Role: role},
				},
			},
		}, false, nil

	case "content-delta":
		if event.Delta == nil {
			return nil, false, nil
		}
		var cd contentDelta
		if err := json.Unmarshal(event.Delta, &cd); err != nil {
			// Try simpler format.
			var simple struct {
				Text string `json:"text"`
			}
			if err2 := json.Unmarshal(event.Delta, &simple); err2 != nil {
				return nil, false, nil
			}
			content := simple.Text
			return &models.StreamChunk{
				ID:      s.chunkID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{
					{
						Index: 0,
						Delta: models.Delta{Content: &content},
					},
				},
			}, false, nil
		}
		content := cd.Message.Content.Text
		return &models.StreamChunk{
			ID:      s.chunkID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{
					Index: 0,
					Delta: models.Delta{Content: &content},
				},
			},
		}, false, nil

	case "tool-call-start":
		return s.handleToolCallStart(event)

	case "tool-call-delta":
		return s.handleToolCallDelta(event)

	case "tool-call-end":
		// No action needed on tool-call-end; the tool call is complete.
		return nil, false, nil

	case "message-end":
		finishReason := mapCohereFinishReason(event.FinishReason)
		chunk := &models.StreamChunk{
			ID:      s.chunkID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{
					Index:        0,
					FinishReason: &finishReason,
				},
			},
		}
		if event.Usage != nil && event.Usage.Tokens != nil {
			chunk.Usage = &models.Usage{
				PromptTokens:     event.Usage.Tokens.InputTokens,
				CompletionTokens: event.Usage.Tokens.OutputTokens,
				TotalTokens:      event.Usage.Tokens.InputTokens + event.Usage.Tokens.OutputTokens,
			}
		}
		return chunk, true, nil
	}

	return nil, false, nil
}

// handleToolCallStart processes a tool-call-start event by emitting a chunk
// with the tool call ID, type, and function name (mirroring OpenAI streaming format).
func (s *streamState) handleToolCallStart(event streamEvent) (*models.StreamChunk, bool, error) {
	if event.Delta == nil {
		return nil, false, nil
	}

	var tcs toolCallStartDelta
	if err := json.Unmarshal(event.Delta, &tcs); err != nil {
		return nil, false, nil
	}

	idx := s.toolCallIndex
	chunk := &models.StreamChunk{
		ID:      s.chunkID,
		Object:  "chat.completion.chunk",
		Model:   s.model,
		Created: s.created,
		Choices: []models.StreamChoice{
			{
				Index: 0,
				Delta: models.Delta{
					ToolCalls: []models.ToolCallDelta{
						{
							Index: idx,
							ID:    tcs.Message.ToolCalls.ID,
							Type:  "function",
							Function: &models.FunctionCall{
								Name:      tcs.Message.ToolCalls.Function.Name,
								Arguments: tcs.Message.ToolCalls.Function.Arguments,
							},
						},
					},
				},
			},
		},
	}
	s.toolCallIndex++
	return chunk, false, nil
}

// handleToolCallDelta processes a tool-call-delta event by emitting argument chunks.
func (s *streamState) handleToolCallDelta(event streamEvent) (*models.StreamChunk, bool, error) {
	if event.Delta == nil {
		return nil, false, nil
	}

	var tcd toolCallDeltaPayload
	if err := json.Unmarshal(event.Delta, &tcd); err != nil {
		return nil, false, nil
	}

	// The current tool call is the last one that was started.
	tcIdx := s.toolCallIndex - 1
	if tcIdx < 0 {
		tcIdx = 0
	}

	chunk := &models.StreamChunk{
		ID:      s.chunkID,
		Object:  "chat.completion.chunk",
		Model:   s.model,
		Created: s.created,
		Choices: []models.StreamChoice{
			{
				Index: 0,
				Delta: models.Delta{
					ToolCalls: []models.ToolCallDelta{
						{
							Index: tcIdx,
							Function: &models.FunctionCall{
								Arguments: tcd.Message.ToolCalls.Function.Arguments,
							},
						},
					},
				},
			},
		},
	}
	return chunk, false, nil
}
