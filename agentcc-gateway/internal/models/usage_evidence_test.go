package models

import (
	"encoding/json"
	"testing"
)

func TestUsageEvidence(t *testing.T) {
	for _, tc := range []struct {
		raw                                string
		prompt, completion, cost, estimate bool
	}{
		{`{}`, false, false, false, true},
		{`{"prompt_tokens":null,"completion_tokens":null}`, false, false, false, true},
		{`{"total_tokens":0,"other_tokens":0}`, false, false, false, true},
		{`{"prompt_tokens":0}`, true, false, false, true},
		{`{"completion_tokens":0}`, false, true, false, true},
		{`{"prompt_tokens":0,"completion_tokens":0}`, true, true, false, false},
		{`{"prompt_tokens":8,"completion_tokens":3}`, true, true, false, false},
		{`{"prompt_tokens":-1,"completion_tokens":3}`, false, true, false, true},
		{`{"cost":0}`, false, false, true, false},
		{`{"cost":0.1}`, false, false, true, false},
		{`{"cost":null}`, false, false, false, true},
		{`{"cost":-1}`, false, false, false, true},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			var u Usage
			if err := json.Unmarshal([]byte(tc.raw), &u); err != nil {
				t.Fatal(err)
			}
			if u.HasPromptTokens() != tc.prompt || u.HasCompletionTokens() != tc.completion || u.HasReportedCost() != tc.cost || u.NeedsTokenEstimate() != tc.estimate {
				t.Fatalf("presence prompt=%t completion=%t cost=%t estimate=%t", u.HasPromptTokens(), u.HasCompletionTokens(), u.HasReportedCost(), u.NeedsTokenEstimate())
			}
		})
	}
	var u *Usage
	if !u.NeedsTokenEstimate() {
		t.Fatal("nil usage is not a report")
	}
	if MeasuredUsage(0, 0).NeedsTokenEstimate() {
		t.Fatal("explicitly measured native zero counts are complete")
	}
}

func TestUsageEvidenceMerge(t *testing.T) {
	var merged *Usage
	for _, raw := range []string{`{"prompt_tokens":8}`, `{"completion_tokens":3}`, `{}`} {
		var u Usage
		if err := json.Unmarshal([]byte(raw), &u); err != nil {
			t.Fatal(err)
		}
		merged = MergeUsage(merged, &u)
	}
	if merged.NeedsTokenEstimate() || merged.PromptTokens != 8 || merged.CompletionTokens != 3 {
		t.Fatalf("lost cumulative evidence: %+v", merged)
	}
	b, err := json.Marshal(merged)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(b, &fields); err != nil {
		t.Fatal(err)
	}
	if len(fields) != 3 {
		t.Fatalf("internal evidence changed wire shape: %s", b)
	}
}
