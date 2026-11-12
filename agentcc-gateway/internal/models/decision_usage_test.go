package models

import "testing"

// A decision's usage is marked measured only for the counts the vendor's
// answer carried: cost and budgets settle on what was reported, and a count
// the vendor left out is never read as a measured zero.
func TestDecisionTokenUsageMarksOnlyReportedCounts(t *testing.T) {
	resp, ok := ParseDecisionResponse([]byte(`{"model":"m","usage":{"input_tokens":17,"output_tokens":3}}`))
	if !ok {
		t.Fatal("answer not parsed")
	}
	u := resp.TokenUsage()
	if !u.HasPromptTokens() || !u.HasCompletionTokens() || u.NeedsTokenEstimate() ||
		u.PromptTokens != 17 || u.CompletionTokens != 3 || u.TotalTokens != 20 {
		t.Fatalf("both counts reported: usage = %+v", u)
	}

	resp, _ = ParseDecisionResponse([]byte(`{"model":"m","usage":{"prompt_tokens":5}}`))
	u = resp.TokenUsage()
	if !u.HasPromptTokens() || u.HasCompletionTokens() || !u.NeedsTokenEstimate() || u.PromptTokens != 5 {
		t.Fatalf("only the input reported: usage = %+v", u)
	}

	resp, _ = ParseDecisionResponse([]byte(`{"model":"m","usage":{"input_tokens":"many","output_tokens":0}}`))
	u = resp.TokenUsage()
	if u.HasPromptTokens() || !u.HasCompletionTokens() {
		t.Fatalf("an unreadable input count is not measured: usage = %+v", u)
	}

	resp, _ = ParseDecisionResponse([]byte(`{"model":"m"}`))
	if u := resp.TokenUsage(); u.HasPromptTokens() || u.HasCompletionTokens() {
		t.Fatalf("no usage reported: usage = %+v", u)
	}
}
