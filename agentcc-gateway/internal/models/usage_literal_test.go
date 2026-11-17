package models

import (
	"encoding/json"
	"testing"
)

// A literal is not proof that an upstream actually sent either counter.
func TestUsageLiteralRequiresEvidence(t *testing.T) {
	for _, u := range []*Usage{{}, {PromptTokens: 5}, {CompletionTokens: 5}, {PromptTokens: 5, CompletionTokens: 3}} {
		if u.HasPromptTokens() || u.HasCompletionTokens() || !u.NeedsTokenEstimate() {
			t.Fatalf("unflagged literal invented metering evidence: %+v", u)
		}
	}
	var measured Usage
	if err := json.Unmarshal([]byte(`{"prompt_tokens":8,"completion_tokens":3}`), &measured); err != nil {
		t.Fatal(err)
	}
	merged := MergeUsage(&measured, &Usage{})
	if merged.NeedsTokenEstimate() || merged.PromptTokens != 8 || merged.CompletionTokens != 3 {
		t.Fatalf("literal erased measured evidence: %+v", merged)
	}
}
