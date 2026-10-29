package tokenizer

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// EstimateTokens estimates the token count for a list of messages.
// Uses a heuristic: ~4 characters per token for English text (close to
// cl100k_base and o200k_base averages). Adds per-message overhead tokens
// for role and formatting (3 tokens per message + 3 tokens for the reply primer).
func EstimateTokens(messages []models.Message) int {
	total := 0
	for _, msg := range messages {
		// Per-message overhead: <|im_start|>{role}\n ... <|im_end|>\n = ~3 tokens.
		total += 3

		// Role token.
		total += estimateStringTokens(msg.Role)

		// Content tokens.
		if msg.Content != nil {
			total += estimateContentTokens(msg.Content)
		}

		// Name field.
		if msg.Name != "" {
			total += estimateStringTokens(msg.Name) + 1 // +1 for separator
		}

		// Tool calls (estimate from serialized JSON).
		if len(msg.ToolCalls) > 0 {
			for _, tc := range msg.ToolCalls {
				total += estimateStringTokens(tc.Function.Name)
				total += estimateStringTokens(tc.Function.Arguments)
				total += 3 // overhead per tool call
			}
		}
	}

	// Reply primer: every reply is primed with <|im_start|>assistant<|im_sep|> = 3 tokens.
	total += 3

	return total
}

// MediaPartTokens is what an image, audio or file part counts as: OpenAI's
// count for a 1024x1024 image at high detail. Its inline bytes, often base64,
// say nothing about its tokens, and counting them as text overstates a prompt
// a hundredfold.
const MediaPartTokens = 765

// estimateContentTokens estimates message content: a string, or an array of
// parts, whose text parts count as text and other parts as MediaPartTokens.
func estimateContentTokens(raw json.RawMessage) int {
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return estimateStringTokens(s)
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err != nil {
		return estimateStringTokens(string(raw))
	}
	total := 0
	for _, part := range parts {
		if part.Type == "text" || part.Text != "" {
			total += estimateStringTokens(part.Text)
		} else {
			total += MediaPartTokens
		}
	}
	return total
}

// estimateStringTokens estimates tokens from a string using the ~4 chars/token heuristic.
// This is a reasonable approximation for cl100k_base and o200k_base encodings.
func estimateStringTokens(s string) int {
	if s == "" {
		return 0
	}

	// Count words and characters.
	words := len(strings.Fields(s))
	chars := len(s)

	// Use the higher of: word-based estimate or char-based estimate.
	// Word-based: ~1.3 tokens per word (average for English).
	// Char-based: ~1 token per 4 characters.
	wordEst := (words * 13) / 10 // 1.3x
	charEst := (chars + 3) / 4   // ceil(chars/4)

	if wordEst > charEst {
		return wordEst
	}
	return charEst
}
