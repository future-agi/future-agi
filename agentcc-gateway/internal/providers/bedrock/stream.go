package bedrock

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"io"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AWS Event Stream decoder for Bedrock streaming responses.
// The event stream is a binary protocol with framed messages.

// eventStreamMessage represents a decoded AWS event stream message.
type eventStreamMessage struct {
	Headers map[string]string
	Payload []byte
}

// streamState tracks state across Bedrock stream events.
type bedrockStreamState struct {
	messageID                 string
	model                     string
	inputTokens               int
	created                   int64
	toolCallIndex             int
	blockIndexToToolCallIndex map[int]int
}

func newBedrockStreamState(model string) *bedrockStreamState {
	return &bedrockStreamState{
		model:                     model,
		created:                   time.Now().Unix(),
		blockIndexToToolCallIndex: make(map[int]int),
	}
}

// readEventStreamMessage reads a single message from the AWS event stream.
// Returns nil on EOF.
func readEventStreamMessage(r io.Reader) (*eventStreamMessage, error) {
	// Read prelude: total_byte_length (4) + headers_byte_length (4).
	prelude := make([]byte, 8)
	if _, err := io.ReadFull(r, prelude); err != nil {
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			return nil, nil
		}
		return nil, fmt.Errorf("reading prelude: %w", err)
	}

	totalLen := binary.BigEndian.Uint32(prelude[0:4])
	headersLen := binary.BigEndian.Uint32(prelude[4:8])

	// Read prelude CRC (4 bytes).
	preludeCRC := make([]byte, 4)
	if _, err := io.ReadFull(r, preludeCRC); err != nil {
		return nil, fmt.Errorf("reading prelude CRC: %w", err)
	}

	// Verify prelude CRC.
	expectedCRC := crc32.ChecksumIEEE(prelude)
	actualCRC := binary.BigEndian.Uint32(preludeCRC)
	if expectedCRC != actualCRC {
		return nil, fmt.Errorf("prelude CRC mismatch: expected %d, got %d", expectedCRC, actualCRC)
	}

	// Read headers.
	headersBytes := make([]byte, headersLen)
	if headersLen > 0 {
		if _, err := io.ReadFull(r, headersBytes); err != nil {
			return nil, fmt.Errorf("reading headers: %w", err)
		}
	}

	headers := parseHeaders(headersBytes)

	// Calculate payload length:
	// totalLen = prelude(8) + preludeCRC(4) + headers(headersLen) + payload(?) + messageCRC(4)
	payloadLen := int(totalLen) - 8 - 4 - int(headersLen) - 4
	if payloadLen < 0 {
		payloadLen = 0
	}

	payload := make([]byte, payloadLen)
	if payloadLen > 0 {
		if _, err := io.ReadFull(r, payload); err != nil {
			return nil, fmt.Errorf("reading payload: %w", err)
		}
	}

	// Read message CRC (4 bytes).
	msgCRC := make([]byte, 4)
	if _, err := io.ReadFull(r, msgCRC); err != nil {
		return nil, fmt.Errorf("reading message CRC: %w", err)
	}

	return &eventStreamMessage{
		Headers: headers,
		Payload: payload,
	}, nil
}

// parseHeaders parses the binary header format used by AWS event streams.
func parseHeaders(data []byte) map[string]string {
	headers := make(map[string]string)
	pos := 0

	for pos < len(data) {
		// Header name: 1-byte length + name bytes.
		if pos >= len(data) {
			break
		}
		nameLen := int(data[pos])
		pos++
		if pos+nameLen > len(data) {
			break
		}
		name := string(data[pos : pos+nameLen])
		pos += nameLen

		// Header value type (1 byte).
		if pos >= len(data) {
			break
		}
		valueType := data[pos]
		pos++

		switch valueType {
		case 0: // Bool (1 byte).
			pos++
		case 1: // Byte (1 byte).
			pos++
		case 2: // Short (2 bytes).
			pos += 2
		case 3: // Int (4 bytes).
			pos += 4
		case 4: // Long (8 bytes).
			pos += 8
		case 5: // Bytes (2-byte length + data).
			if pos+2 > len(data) {
				return headers
			}
			valueLen := int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2 + valueLen
		case 7: // String (2-byte length + data).
			if pos+2 > len(data) {
				return headers
			}
			valueLen := int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2
			if pos+valueLen > len(data) {
				return headers
			}
			headers[name] = string(data[pos : pos+valueLen])
			pos += valueLen
		case 8: // Timestamp (8 bytes).
			pos += 8
		case 9: // UUID (16 bytes).
			pos += 16
		default:
			// Truly unknown type — can't determine size, stop parsing.
			return headers
		}
	}

	return headers
}

// Bedrock stream chunk envelope (for Claude on Bedrock).
type bedrockStreamChunk struct {
	Type  string          `json:"type"`
	Bytes json.RawMessage `json:"bytes,omitempty"`
}

// parseStreamPayload parses a Bedrock event stream payload into a StreamChunk.
func (s *bedrockStreamState) parseStreamPayload(msg *eventStreamMessage) (*models.StreamChunk, bool, error) {
	messageType := msg.Headers[":message-type"]
	if messageType == "exception" {
		errMsg := string(msg.Payload)
		return nil, false, fmt.Errorf("bedrock exception: %s", errMsg)
	}

	if messageType != "event" {
		return nil, false, nil
	}

	// The payload for Bedrock Claude streaming is the Anthropic event JSON.
	// Parse it as a stream event.
	var event struct {
		Type    string          `json:"type"`
		Message json.RawMessage `json:"message,omitempty"`
		Delta   json.RawMessage `json:"delta,omitempty"`
		Index   int             `json:"index,omitempty"`
		Usage   *bedrockUsage   `json:"usage,omitempty"`
	}

	if err := json.Unmarshal(msg.Payload, &event); err != nil {
		return nil, false, nil
	}

	switch event.Type {
	case "message_start":
		var msgStart struct {
			ID    string       `json:"id"`
			Model string       `json:"model"`
			Usage bedrockUsage `json:"usage"`
		}
		if event.Message != nil {
			json.Unmarshal(event.Message, &msgStart)
		}
		s.messageID = msgStart.ID
		if msgStart.Model != "" {
			s.model = msgStart.Model
		}
		s.inputTokens = msgStart.Usage.InputTokens

		role := "assistant"
		return &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{Index: 0, Delta: models.Delta{Role: role}},
			},
		}, false, nil

	case "content_block_delta":
		if event.Delta == nil {
			return nil, false, nil
		}
		var textDelta struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}
		json.Unmarshal(event.Delta, &textDelta)
		if textDelta.Type == "text_delta" && textDelta.Text != "" {
			content := textDelta.Text
			return &models.StreamChunk{
				ID:      s.messageID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{
					{Index: 0, Delta: models.Delta{Content: &content}},
				},
			}, false, nil
		}

		var inputJSONDelta struct {
			Type        string `json:"type"`
			PartialJSON string `json:"partial_json"`
		}
		json.Unmarshal(event.Delta, &inputJSONDelta)
		if inputJSONDelta.Type == "input_json_delta" {
			tcIdx, ok := s.blockIndexToToolCallIndex[event.Index]
			if !ok {
				tcIdx = 0
			}
			return &models.StreamChunk{
				ID:      s.messageID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{
					{Index: 0, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{
						Index:    tcIdx,
						Function: &models.FunctionCall{Arguments: inputJSONDelta.PartialJSON},
					}}}},
				},
			}, false, nil
		}
		return nil, false, nil

	case "content_block_start":
		if event.Message != nil {
			return nil, false, nil
		}
		var block struct {
			Type string `json:"type"`
			ID   string `json:"id,omitempty"`
			Name string `json:"name,omitempty"`
		}
		var payload struct {
			ContentBlock struct {
				Type string `json:"type"`
				ID   string `json:"id,omitempty"`
				Name string `json:"name,omitempty"`
			} `json:"content_block"`
		}
		json.Unmarshal(msg.Payload, &payload)
		block = payload.ContentBlock
		if block.Type == "tool_use" {
			idx := s.toolCallIndex
			s.toolCallIndex++
			s.blockIndexToToolCallIndex[event.Index] = idx
			return &models.StreamChunk{
				ID:      s.messageID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{
					{Index: 0, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{
						Index:    idx,
						ID:       block.ID,
						Type:     "function",
						Function: &models.FunctionCall{Name: block.Name, Arguments: ""},
					}}}},
				},
			}, false, nil
		}
		return nil, false, nil

	case "message_delta":
		if event.Delta == nil {
			return nil, false, nil
		}
		var delta struct {
			StopReason string `json:"stop_reason"`
		}
		json.Unmarshal(event.Delta, &delta)
		finishReason := mapStopReason(delta.StopReason)

		chunk := &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{
				{Index: 0, FinishReason: &finishReason},
			},
		}
		if event.Usage != nil {
			chunk.Usage = &models.Usage{
				PromptTokens:     s.inputTokens,
				CompletionTokens: event.Usage.OutputTokens,
				TotalTokens:      s.inputTokens + event.Usage.OutputTokens,
			}
		}
		return chunk, false, nil

	case "message_stop":
		return nil, true, nil
	}

	return nil, false, nil
}
