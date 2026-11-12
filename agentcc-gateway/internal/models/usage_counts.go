package models

// NewUsage preserves the provider's counter presence, including explicit zero.
// A nil or negative counter is not a measurement. Total-only, cache-only and
// unflagged Usage literals cannot establish either input/output dimension.
func NewUsage(prompt, completion *int) *Usage {
	u := &Usage{}
	if prompt != nil {
		u.PromptTokens = *prompt
		u.promptReported = *prompt >= 0
	}
	if completion != nil {
		u.CompletionTokens = *completion
		u.completionReported = *completion >= 0
	}
	u.TotalTokens = u.PromptTokens + u.CompletionTokens
	return u
}

// SetCompletionTokenFloor retains a measured output component while leaving
// the missing dimension unmeasured. Only adapters with actual evidence call it.
func (u *Usage) SetCompletionTokenFloor(floor int) {
	u.completionFloor = max(u.completionFloor, floor)
	u.CompletionTokens = max(u.CompletionTokens, u.completionFloor)
	if u.completionFloor > 0 {
		u.TotalTokens = max(u.TotalTokens, max(0, u.PromptTokens)+u.CompletionTokens)
	}
}

// EstimateCompletionTokens fills the missing candidates dimension on a copy
// of provider usage, retaining the measured floor and explicit provenance.
func (u *Usage) EstimateCompletionTokens(estimate int) {
	u.CompletionTokens = u.completionFloor + max(0, estimate)
	u.completionReported = true
	u.Estimated = true
}

// CompletionTokenFloor is added to the estimate of the missing candidates,
// never substituted for a complete measured output count.
func (u *Usage) CompletionTokenFloor() int {
	if u == nil {
		return 0
	}
	return u.completionFloor
}

// MeasuredUsage is for producers that already know both counts are present.
// Wire adapters should use NewUsage with decoded pointers instead. Estimates
// also use this constructor, but must set Estimated to retain their provenance.
func MeasuredUsage(prompt, completion int) *Usage {
	return NewUsage(&prompt, &completion)
}

// AddTokenCount folds separately reported cache/reasoning tokens into a known
// input/output count. The extra dimension alone does not prove the base count.
func AddTokenCount(base *int, extra int) *int {
	if base == nil || *base < 0 {
		return base
	}
	total := *base + extra
	return &total
}

// MaxTokenCount merges cumulative stream counters without inventing zero for
// an absent dimension. Neither input is mutated.
func MaxTokenCount(previous, next *int) *int {
	if previous == nil || *previous < 0 {
		return next
	}
	if next == nil || *next < 0 || *previous > *next {
		return previous
	}
	return next
}
