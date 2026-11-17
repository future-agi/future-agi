package models

import "math"

// HasReportedCost distinguishes an explicit valid zero charge from missing or
// invalid cost. Neither an unsupported extra field nor total_tokens can price
// the input/output dimensions of a token card.
func (u *Usage) HasReportedCost() bool {
	return u != nil && u.Cost != nil && *u.Cost >= 0 && !math.IsNaN(*u.Cost) && !math.IsInf(*u.Cost, 0)
}

// Presence is explicit on every path. Neither a decoded empty object nor an
// unflagged native literal can create measured zero counters.
func (u *Usage) HasPromptTokens() bool {
	return u != nil && u.PromptTokens >= 0 && u.promptReported
}

func (u *Usage) HasCompletionTokens() bool {
	return u != nil && u.CompletionTokens >= 0 && u.completionReported
}

// CostContested reports that a later event explicitly reported an invalid cost
// after the valid total u carries. A later valid total clears it.
func (u *Usage) CostContested() bool { return u != nil && u.costContested }

func (u *Usage) NeedsTokenEstimate() bool {
	return !u.HasReportedCost() && (!u.HasPromptTokens() || !u.HasCompletionTokens())
}

// MergeUsage retains evidence across cumulative stream events. An empty trailing
// object cannot erase earlier measurements; partial reports replace only the
// dimensions they actually measured. Neither input is mutated.
func MergeUsage(previous, next *Usage) *Usage {
	if next == nil {
		return previous
	}
	if previous == nil {
		return next
	}
	merged := *next
	if !next.HasPromptTokens() && previous.HasPromptTokens() {
		merged.PromptTokens, merged.promptReported = previous.PromptTokens, true
	}
	if !next.HasCompletionTokens() && previous.HasCompletionTokens() && next.completionFloor == 0 {
		merged.CompletionTokens, merged.completionReported = previous.CompletionTokens, true
	}
	floor := max(previous.completionFloor, next.completionFloor)
	if !next.HasCompletionTokens() && next.completionFloor > 0 && !previous.Estimated {
		// A newer partial output reading cannot inherit an earlier complete
		// (possibly zero) count's presence. Keep its evidence as a floor only.
		floor = max(floor, previous.CompletionTokens)
	}
	merged.SetCompletionTokenFloor(floor)
	if !next.HasReportedCost() && previous.HasReportedCost() {
		// Cost and its wire lexeme always move together: a later null, string,
		// negative or non-numeric cost never replaces an earlier valid total,
		// nor leaves the pair disagreeing. An explicit one (unlike an omitted
		// cost) contests that total: inclusive settlement treats it as unknown
		// (full hold, pending reconciliation), never as the earlier partial.
		merged.Cost, merged.CostDecimal = previous.Cost, previous.CostDecimal
		merged.costContested = previous.costContested || next.Cost != nil || next.CostDecimal != ""
	}
	if merged.PromptTokensDetails == nil {
		merged.PromptTokensDetails = previous.PromptTokensDetails
		merged.PromptDetails = previous.PromptDetails
	}
	if merged.CompletionTokensDetails == nil {
		merged.CompletionTokensDetails = previous.CompletionTokensDetails
		merged.CompletionDetails = previous.CompletionDetails
	}
	if merged.ServerToolUse == nil {
		merged.ServerToolUse = previous.ServerToolUse
	}
	merged.Media = MergeMediaEvidence(previous.Media, next.Media)
	merged.Estimated = merged.Estimated || previous.Estimated
	return &merged
}
