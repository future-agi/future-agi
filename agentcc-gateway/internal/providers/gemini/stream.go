package gemini

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// streamState tracks state across Gemini stream events.
type streamState struct {
	model       string
	chunkID     string
	created     int64
	toolCallIdx int // tracks incrementing index for tool calls across chunks
}

func newStreamState(model string) *streamState {
	now := time.Now()
	return &streamState{
		model:   model,
		chunkID: fmt.Sprintf("gen-%d", now.UnixNano()),
		created: now.Unix(),
	}
}

// parseStreamData parses a Gemini SSE data payload and returns a StreamChunk.
func (s *streamState) parseStreamData(data string) (*models.StreamChunk, error) {
	var resp geminiResponse
	if err := json.Unmarshal([]byte(data), &resp); err != nil {
		return nil, err
	}

	chunk := &models.StreamChunk{
		ID:      s.chunkID,
		Object:  "chat.completion.chunk",
		Model:   s.model,
		Created: s.created,
	}

	if len(resp.Candidates) > 0 {
		candidate := resp.Candidates[0]
		delta := models.Delta{}

		// Concatenate text across all parts so multi-part chunks don't drop
		// earlier text (previous impl was last-wins, which silently lost
		// content when a chunk held text followed by a function call or image).
		var textBuilder string
		hasText := false
		for _, part := range candidate.Content.Parts {
			if part.Text != "" {
				textBuilder += part.Text
				hasText = true
			}
			if part.InlineData != nil {
				// delta.Content is *string, so we encode the image as a
				// data-URI placeholder appended to any preceding text.
				dataURI := fmt.Sprintf("![image](data:%s;base64,%s)", part.InlineData.MimeType, part.InlineData.Data)
				textBuilder += dataURI
				hasText = true
			}
			if part.FunctionCall != nil {
				delta.ToolCalls = append(delta.ToolCalls, models.ToolCallDelta{
					Index: s.toolCallIdx,
					ID:    fmt.Sprintf("call_%d", s.toolCallIdx),
					Type:  "function",
					Function: &models.FunctionCall{
						Name:      part.FunctionCall.Name,
						Arguments: string(part.FunctionCall.Args),
					},
				})
				s.toolCallIdx++
			}
		}
		if hasText {
			// Emit the content field whenever any text-bearing part was
			// present — even if it accumulated to an empty string — so SDK
			// consumers that check for field presence rather than null get a
			// consistent shape across chunks.
			content := textBuilder
			delta.Content = &content
		}

		sc := models.StreamChoice{
			Index: 0,
			Delta: delta,
		}

		if candidate.FinishReason != "" {
			reason := mapGeminiFinishReasonWithToolCalls(candidate.FinishReason, len(delta.ToolCalls) > 0)
			sc.FinishReason = &reason
		}

		chunk.Choices = []models.StreamChoice{sc}
	}

	if resp.UsageMetadata != nil {
		chunk.Usage = &models.Usage{
			PromptTokens:     resp.UsageMetadata.PromptTokenCount,
			CompletionTokens: resp.UsageMetadata.CandidatesTokenCount,
			TotalTokens:      resp.UsageMetadata.TotalTokenCount,
		}
	}

	return chunk, nil
}
