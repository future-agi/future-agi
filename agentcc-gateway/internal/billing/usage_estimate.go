package billing

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// EstimateUsage uses the same prompt estimator as reservation, including
// tools and structured-response schemas, and four UTF-8 bytes per output
// token. It is accounting provenance, not a provider-reported measurement.
func EstimateUsage(req *models.ChatCompletionRequest, outputBytes int) *models.Usage {
	prompt, completion := EstimateInputTokens(req), (outputBytes+3)/4
	u := models.MeasuredUsage(prompt, completion)
	u.Estimated = true
	return u
}

// CompleteUsage fills only unmeasured token dimensions. Explicit counters,
// including zero, and any valid reported cost always win. A total-only report
// cannot allocate input/output prices, so it needs the same auditable estimate.
func CompleteUsage(reported, estimate *models.Usage) *models.Usage {
	if !reported.NeedsTokenEstimate() || estimate == nil {
		return reported
	}
	if reported == nil {
		return estimate
	}
	estimated := *estimate
	estimated.Estimated = true
	merged := *models.MergeUsage(&estimated, reported)
	if !reported.HasCompletionTokens() && reported.CompletionTokenFloor() > 0 {
		// The output estimate covers the unmeasured candidates. Separately
		// measured thoughts must survive even an empty MAX_TOKENS response.
		merged.EstimateCompletionTokens(estimate.CompletionTokens)
	}
	merged.TotalTokens = merged.PromptTokens + merged.CompletionTokens
	merged.Estimated = true
	return &merged
}

// EstimateResponseUsage mirrors the stream estimate for a successful text or
// tool answer without usage. No estimate replaces an explicit usage report.
func EstimateResponseUsage(req *models.ChatCompletionRequest, resp *models.ChatCompletionResponse) *models.Usage {
	if req == nil || resp == nil || len(resp.Choices) == 0 {
		return nil
	}
	n := 0
	for _, choice := range resp.Choices {
		m := choice.Message
		var text string
		if len(m.Content) == 0 || string(m.Content) == "null" {
			// Tool-only responses have no content string.
		} else if json.Unmarshal(m.Content, &text) == nil {
			n += len(text)
		} else {
			var parts []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}
			if json.Unmarshal(m.Content, &parts) != nil {
				return nil
			}
			for _, part := range parts {
				if part.Type != "text" {
					return nil
				}
				n += len(part.Text)
			}
		}
		n += len(m.ReasoningContent)
		for _, tc := range m.ToolCalls {
			n += len(tc.Function.Name) + len(tc.Function.Arguments)
		}
	}
	return EstimateUsage(req, n)
}
