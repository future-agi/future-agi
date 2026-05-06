package anthropic

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// streamState tracks accumulated state across stream events.
type streamState struct {
	messageID   string
	model       string
	inputTokens int
	created     int64

	// toolCallIndex tracks the current tool call index for streaming tool calls.
	// Each content_block_start of type "tool_use" increments this.
	toolCallIndex int
	// blockIsToolUse records whether the current content block is a tool_use block,
	// so that input_json_delta events can use the correct index.
	blockIsToolUse bool
	// currentBlockIndex records the Anthropic content block index for the active block.
	currentBlockIndex int
}

func newStreamState() *streamState {
	return &streamState{created: time.Now().Unix()}
}

// Anthropic stream event types.
type streamEvent struct {
	Type    string          `json:"type"`
	Message json.RawMessage `json:"message,omitempty"`
	Index   int             `json:"index,omitempty"`
	Delta   json.RawMessage `json:"delta,omitempty"`
	Usage   *anthropicUsage `json:"usage,omitempty"`

	ContentBlock json.RawMessage `json:"content_block,omitempty"`
}

type messageStartData struct {
	ID    string         `json:"id"`
	Model string         `json:"model"`
	Usage anthropicUsage `json:"usage"`
}

type textDelta struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type inputJSONDelta struct {
	Type        string `json:"type"`
	PartialJSON string `json:"partial_json"`
}

type messageDeltaData struct {
	StopReason string `json:"stop_reason"`
}

type contentBlockStartData struct {
	Type  string `json:"type"`
	ID    string `json:"id,omitempty"`
	Name  string `json:"name,omitempty"`
}

// parseSSELine parses a single SSE data line and returns a StreamChunk if one should be emitted.
// Returns (chunk, done, error).
func (s *streamState) parseSSELine(eventType, data string) (*models.StreamChunk, bool, error) {
	if data == "" {
		return nil, false, nil
	}

	var event streamEvent
	if err := json.Unmarshal([]byte(data), &event); err != nil {
		return nil, false, nil // Skip unparseable events.
	}

	switch event.Type {
	case "message_start":
		return s.handleMessageStart(event)
	case "content_block_start":
		return s.handleContentBlockStart(event)
	case "content_block_delta":
		return s.handleContentBlockDelta(event)
	case "content_block_stop":
		return nil, false, nil
	case "message_delta":
		return s.handleMessageDelta(event)
	case "message_stop":
		return nil, true, nil
	case "ping":
		return nil, false, nil
	case "error":
		return nil, false, nil
	}

	return nil, false, nil
}

func (s *streamState) handleMessageStart(event streamEvent) (*models.StreamChunk, bool, error) {
	if event.Message == nil {
		return nil, false, nil
	}

	var msg messageStartData
	if err := json.Unmarshal(event.Message, &msg); err != nil {
		return nil, false, nil
	}

	s.messageID = msg.ID
	s.model = msg.Model
	s.inputTokens = msg.Usage.InputTokens

	role := "assistant"
	chunk := &models.StreamChunk{
		ID:      s.messageID,
		Object:  "chat.completion.chunk",
		Model:   s.model,
		Created: s.created,
		Choices: []models.StreamChoice{
			{
				Index: 0,
				Delta: models.Delta{
					Role: role,
				},
			},
		},
	}
	return chunk, false, nil
}

func (s *streamState) handleContentBlockStart(event streamEvent) (*models.StreamChunk, bool, error) {
	s.currentBlockIndex = event.Index
	s.blockIsToolUse = false

	if event.ContentBlock == nil {
		return nil, false, nil
	}

	var block contentBlockStartData
	if err := json.Unmarshal(event.ContentBlock, &block); err != nil {
		return nil, false, nil
	}

	if block.Type == "tool_use" {
		s.blockIsToolUse = true
		// Emit a chunk with the tool call ID, type, and function name.
		// The first chunk for a tool call carries id, type, and function.name.
		idx := s.toolCallIndex
		chunk := &models.StreamChunk{
			ID:      s.messageID,
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
								ID:    block.ID,
								Type:  "function",
								Function: &models.FunctionCall{
									Name:      block.Name,
									Arguments: "",
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

	// We don't emit a chunk for content_block_start of type "text".
	return nil, false, nil
}

func (s *streamState) handleContentBlockDelta(event streamEvent) (*models.StreamChunk, bool, error) {
	if event.Delta == nil {
		return nil, false, nil
	}

	// Try text_delta.
	var td textDelta
	if err := json.Unmarshal(event.Delta, &td); err == nil && td.Type == "text_delta" {
		content := td.Text
		chunk := &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{
					Index: 0,
					Delta: models.Delta{
						Content: &content,
					},
				},
			},
		}
		return chunk, false, nil
	}

	// Try input_json_delta (tool use arguments streaming).
	var ijd inputJSONDelta
	if err := json.Unmarshal(event.Delta, &ijd); err == nil && ijd.Type == "input_json_delta" {
		// Use the tracked tool call index (0-based among tool_use blocks),
		// not the Anthropic content block index which counts all block types.
		tcIdx := s.toolCallIndex - 1
		if tcIdx < 0 {
			tcIdx = 0
		}
		chunk := &models.StreamChunk{
			ID:      s.messageID,
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
									Arguments: ijd.PartialJSON,
								},
							},
						},
					},
				},
			},
		}
		return chunk, false, nil
	}

	return nil, false, nil
}

func (s *streamState) handleMessageDelta(event streamEvent) (*models.StreamChunk, bool, error) {
	if event.Delta == nil {
		return nil, false, nil
	}

	var delta messageDeltaData
	if err := json.Unmarshal(event.Delta, &delta); err != nil {
		return nil, false, nil
	}

	finishReason := mapStopReason(delta.StopReason)

	chunk := &models.StreamChunk{
		ID:      s.messageID,
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

	// Include usage if available.
	if event.Usage != nil {
		chunk.Usage = &models.Usage{
			PromptTokens:     s.inputTokens,
			CompletionTokens: event.Usage.OutputTokens,
			TotalTokens:      s.inputTokens + event.Usage.OutputTokens,
		}
	}

	return chunk, false, nil
}

// parseSSELines processes raw SSE lines from the stream.
// It extracts event type and data, then delegates to parseSSELine.
func parseSSELines(lines []string, state *streamState) ([]*models.StreamChunk, bool, error) {
	var chunks []*models.StreamChunk
	var currentEvent string
	var currentData string

	for _, line := range lines {
		line = strings.TrimSpace(line)

		if line == "" {
			// Empty line = end of event.
			if currentData != "" {
				chunk, done, err := state.parseSSELine(currentEvent, currentData)
				if err != nil {
					return chunks, false, err
				}
				if chunk != nil {
					chunks = append(chunks, chunk)
				}
				if done {
					return chunks, true, nil
				}
			}
			currentEvent = ""
			currentData = ""
			continue
		}

		if strings.HasPrefix(line, "event: ") {
			currentEvent = strings.TrimPrefix(line, "event: ")
		} else if strings.HasPrefix(line, "data: ") {
			currentData = strings.TrimPrefix(line, "data: ")
		}
	}

	// Process any remaining data.
	if currentData != "" {
		chunk, done, err := state.parseSSELine(currentEvent, currentData)
		if err != nil {
			return chunks, false, err
		}
		if chunk != nil {
			chunks = append(chunks, chunk)
		}
		if done {
			return chunks, true, nil
		}
	}

	return chunks, false, nil
}
