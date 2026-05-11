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
			var s string
			if err := json.Unmarshal(msg.Content, &s); err == nil {
				total += estimateStringTokens(s)
			} else {
				// Content may be an array (multimodal). Estimate from raw JSON length.
				total += estimateStringTokens(string(msg.Content))
			}
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
