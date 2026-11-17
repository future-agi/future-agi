package genaifmt

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ExtractUsageEvidence is the presence-preserving view of a non-streamed
// native BYOK passthrough body, a reading that closes generation. A
// thinking-only reading does not prove candidates were zero.
func ExtractUsageEvidence(body []byte) *models.Usage {
	usage, _ := readingEvidence(body, true)
	return usage
}

// readingEvidence decodes one generateContent reading. terminal is whether
// generation had already closed before it; the reading closes it too when
// TerminalReading accepts its candidates or prompt feedback. The result
// reports whether generation is closed after this reading.
func readingEvidence(body []byte, terminal bool) (*models.Usage, bool) {
	var response struct {
		Candidates []struct {
			FinishReason string `json:"finishReason"`
		} `json:"candidates"`
		PromptFeedback *struct {
			BlockReason string `json:"blockReason"`
		} `json:"promptFeedback"`
		Usage *struct {
			Prompt     *int `json:"promptTokenCount"`
			Completion *int `json:"candidatesTokenCount"`
			Thoughts   int  `json:"thoughtsTokenCount"`
			Total      int  `json:"totalTokenCount"`
			ToolPrompt int  `json:"toolUsePromptTokenCount"`
		} `json:"usageMetadata"`
	}
	if json.Unmarshal(body, &response) != nil {
		return nil, terminal
	}
	reasons := make([]string, len(response.Candidates))
	for i, c := range response.Candidates {
		reasons[i] = c.FinishReason
	}
	block := ""
	if response.PromptFeedback != nil {
		block = response.PromptFeedback.BlockReason
	}
	terminal = terminal || TerminalReading(reasons, block)
	if response.Usage == nil {
		return nil, terminal
	}
	raw := response.Usage
	return TokenUsage(raw.Prompt, raw.Completion, raw.Total, raw.Thoughts, raw.ToolPrompt, terminal), terminal
}

// UsageEvidence merges only dimensions actually reported in the retained
// window. An empty trailing usageMetadata cannot erase an earlier reading.
// A frame at or after the one that closed generation is terminal; one
// before it is interim and never proves omitted candidates.
func (s *StreamUsageScanner) UsageEvidence() *models.Usage {
	var usage *models.Usage
	terminal := false
	for _, line := range sseDataLines(s.tail) {
		var reading *models.Usage
		reading, terminal = readingEvidence(line, terminal)
		usage = models.MergeUsage(usage, reading)
	}
	return usage
}
