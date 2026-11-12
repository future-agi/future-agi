package genaifmt

import "github.com/futureagi/agentcc-gateway/internal/models"

// TokenUsage is shared by the Gemini adapter and native BYOK passthrough.
// Gemini's total includes prompt, tool-use prompt, candidates and thoughts,
// and its JSON omits zero counters: an absent thoughtsTokenCount is 0, and an
// absent candidatesTokenCount is 0 or not yet counted. terminal is true for a
// reading that closes generation (a non-streamed response, or a stream frame
// TerminalReading accepts). Only such a reading derives the missing
// candidates from a consistent measured total, zero included (SAFETY with
// content {}, STOP with empty parts, a blocked prompt). An interim frame's
// total can lag text it already carried, so a stream cut after it keeps its
// candidates unmeasured. Otherwise thoughts are retained as a floor and
// billing estimates the candidates. An absent or null total decodes as 0, so
// total > 0 keeps it from proving anything.
func TokenUsage(prompt, candidates *int, total, thoughts, toolPrompt int, terminal bool) *models.Usage {
	thoughts = max(0, thoughts)
	completion := models.AddTokenCount(candidates, thoughts)
	if candidates == nil && terminal && prompt != nil && *prompt >= 0 && toolPrompt >= 0 && total > 0 && total >= *prompt {
		// Stepwise, so hostile counters cannot overflow into a remainder.
		remainder := total - *prompt
		if remainder >= toolPrompt && remainder-toolPrompt >= thoughts {
			// candidates = remainder - toolPrompt - thoughts (nonnegative).
			// Canonical completion includes both candidates and thoughts.
			count := remainder - toolPrompt
			completion = &count
		}
	}
	u := models.NewUsage(prompt, completion)
	if !u.HasCompletionTokens() && thoughts > 0 {
		u.SetCompletionTokenFloor(thoughts)
	}
	if total != 0 {
		u.TotalTokens = max(total, u.TotalTokens)
	}
	u.CompletionDetails.ReasoningTokens = thoughts
	u.MergeDetails()
	return u
}

// TerminalReading reports whether a streamed generateContent frame closes
// generation: every candidate carries a finishReason, or the prompt itself
// was blocked (promptFeedback.blockReason, no candidates), so no further
// output can follow it.
func TerminalReading(finishReasons []string, blockReason string) bool {
	if len(finishReasons) == 0 {
		return blockReason != ""
	}
	for _, reason := range finishReasons {
		if reason == "" {
			return false
		}
	}
	return true
}
