package gemini

import (
	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// toUsage reads the counters of one response or stream frame. terminal is
// whether that reading closes generation (genaifmt.TokenUsage).
func (u *geminiUsageMetadata) toUsage(terminal bool) *models.Usage {
	usage := genaifmt.TokenUsage(u.PromptTokenCount, u.CandidatesTokenCount, u.TotalTokenCount, u.ThoughtsTokenCount, u.ToolUsePromptTokenCount, terminal)
	setTokenDetails(usage, u.CachedContentTokenCount, max(0, u.ThoughtsTokenCount))
	return usage
}

// promptBlocked reports a prompt-level block: Gemini generated no candidate
// because the prompt itself was refused (promptFeedback.blockReason).
func (r *geminiResponse) promptBlocked() bool {
	return len(r.Candidates) == 0 && r.PromptFeedback != nil && r.PromptFeedback.BlockReason != ""
}

// terminal reports whether this stream frame closes generation.
func (r *geminiResponse) terminal() bool {
	reasons := make([]string, len(r.Candidates))
	for i := range r.Candidates {
		reasons[i] = r.Candidates[i].FinishReason
	}
	block := ""
	if r.PromptFeedback != nil {
		block = r.PromptFeedback.BlockReason
	}
	return genaifmt.TerminalReading(reasons, block)
}

// promptBlockedFinish is the finish reason of a blocked prompt's answer: the
// same content_filter a SAFETY-finished candidate maps to.
const promptBlockedFinish = "content_filter"
