package anthropic

import (
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// translateRequest preserves MiniMax's thinking options and client-side history
// when the gateway translates a chat request to the Messages API.
func (p *Provider) translateRequest(req *models.ChatCompletionRequest) (*anthropicRequest, error) {
	ar, err := translateRequest(req)
	if err != nil || !p.minimax {
		return ar, err
	}
	ar.Thinking = req.Extra["thinking"]
	if raw := req.Extra["output_config"]; len(raw) > 0 {
		if err := json.Unmarshal(raw, &ar.OutputConfig); err != nil {
			return nil, fmt.Errorf("invalid output_config: %w", err)
		}
	}
	if raw := req.Extra["reasoning_effort"]; len(raw) > 0 {
		if ar.OutputConfig == nil {
			ar.OutputConfig = &anthropicOutputConfig{}
		}
		if ar.OutputConfig.Effort == "" {
			if err := json.Unmarshal(raw, &ar.OutputConfig.Effort); err != nil {
				return nil, fmt.Errorf("invalid reasoning_effort: %w", err)
			}
		}
	}
	index := 0
	for _, msg := range req.Messages {
		if msg.Role == "system" {
			continue
		}
		if msg.Role == "assistant" {
			var thinking []json.RawMessage
			if len(msg.ThinkingBlocks) > 0 {
				if err := json.Unmarshal(msg.ThinkingBlocks, &thinking); err != nil {
					return nil, fmt.Errorf("invalid thinking_blocks: %w", err)
				}
			} else if msg.ReasoningContent != "" {
				block, _ := json.Marshal(anthropicContentBlock{Type: "thinking", Thinking: msg.ReasoningContent})
				thinking = append(thinking, block)
			}
			if len(thinking) > 0 {
				var content []json.RawMessage
				if err := json.Unmarshal(ar.Messages[index].Content, &content); err != nil {
					return nil, fmt.Errorf("invalid assistant content: %w", err)
				}
				ar.Messages[index].Content, err = json.Marshal(append(thinking, content...))
				if err != nil {
					return nil, err
				}
			}
		}
		index++
	}
	return ar, nil
}
