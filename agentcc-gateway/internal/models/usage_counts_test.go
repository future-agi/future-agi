package models

import "testing"

func TestUsagePresenceConstructors(t *testing.T) {
	zero, in, out, invalid := 0, 8, 3, -1
	for _, tc := range []struct {
		name                     string
		prompt, completion       *int
		hasPrompt, hasCompletion bool
	}{
		{"missing", nil, nil, false, false},
		{"explicit-zero", &zero, &zero, true, true},
		{"prompt-only", &in, nil, true, false},
		{"output-only", nil, &out, false, true},
		{"invalid-input", &invalid, &out, false, true},
		{"complete", &in, &out, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			u := NewUsage(tc.prompt, tc.completion)
			if u.HasPromptTokens() != tc.hasPrompt || u.HasCompletionTokens() != tc.hasCompletion || u.NeedsTokenEstimate() == (tc.hasPrompt && tc.hasCompletion) {
				t.Fatalf("wrong evidence: %+v", u)
			}
		})
	}
	if AddTokenCount(nil, 100) != nil {
		t.Fatal("cache-only fabricated input")
	}
	if got := AddTokenCount(&zero, 10); got == nil || *got != 10 || zero != 0 {
		t.Fatal("explicit base zero was lost or mutated")
	}
	if MaxTokenCount(nil, nil) != nil || MaxTokenCount(nil, &zero) != &zero || MaxTokenCount(&in, &out) != &in || MaxTokenCount(&invalid, &zero) != &zero {
		t.Fatal("cumulative presence merge")
	}
	measured := MeasuredUsage(8, 3)
	merged := MergeUsage(measured, &Usage{PromptTokens: 100})
	if merged.NeedsTokenEstimate() || merged.PromptTokens != 8 || merged.CompletionTokens != 3 {
		t.Fatalf("unflagged literal overwrote evidence: %+v", merged)
	}
}
