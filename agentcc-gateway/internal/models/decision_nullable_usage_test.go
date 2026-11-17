package models

import "testing"

func TestDecisionUsageNullableCountersAndAliases(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		in, out    int
		hasIn      bool
		hasOut     bool
	}{
		{"omitted", `{}`, 0, 0, false, false},
		{"empty", `{"usage":{}}`, 0, 0, false, false},
		{"null_usage", `{"usage":null}`, 0, 0, false, false},
		{"null_counters", `{"usage":{"input_tokens":null,"output_tokens":null}}`, 0, 0, false, false},
		{"negative_counters", `{"usage":{"input_tokens":-1,"output_tokens":-2}}`, 0, 0, false, false},
		{"partial_zero", `{"usage":{"output_tokens":0}}`, 0, 0, false, true},
		{"explicit_zero", `{"usage":{"input_tokens":0,"output_tokens":0}}`, 0, 0, true, true},
		{"aliases", `{"usage":{"prompt_tokens":17,"completion_tokens":3}}`, 17, 3, true, true},
		{"null_primary_valid_alias", `{"usage":{"input_tokens":null,"prompt_tokens":17,"output_tokens":null,"completion_tokens":3}}`, 17, 3, true, true},
		{"negative_primary_valid_alias", `{"usage":{"input_tokens":-1,"prompt_tokens":17,"output_tokens":-2,"completion_tokens":3}}`, 17, 3, true, true},
		{"invalid_primary_valid_alias", `{"usage":{"input_tokens":"synthetic-invalid","prompt_tokens":17,"output_tokens":1.5,"completion_tokens":3}}`, 17, 3, true, true},
		{"invalid_alias", `{"usage":{"input_tokens":null,"prompt_tokens":false,"output_tokens":-1,"completion_tokens":"synthetic-invalid"}}`, 0, 0, false, false},
		{"primary_zero_wins", `{"usage":{"input_tokens":0,"prompt_tokens":17,"output_tokens":0,"completion_tokens":3}}`, 0, 0, true, true},
		{"mixed_presence", `{"usage":{"input_tokens":17,"output_tokens":null}}`, 17, 0, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			resp, ok := ParseDecisionResponse([]byte(tc.body))
			if !ok {
				t.Fatal("synthetic object response was rejected")
			}
			u := resp.TokenUsage()
			if resp.Usage.InputTokens != tc.in || resp.Usage.OutputTokens != tc.out || u.PromptTokens != tc.in || u.CompletionTokens != tc.out {
				t.Fatalf("decision usage = %+v, token usage = %+v; want %d/%d", resp.Usage, u, tc.in, tc.out)
			}
			if u.HasPromptTokens() != tc.hasIn || u.HasCompletionTokens() != tc.hasOut {
				t.Fatalf("presence = %v/%v, want %v/%v", u.HasPromptTokens(), u.HasCompletionTokens(), tc.hasIn, tc.hasOut)
			}
			if u.NeedsTokenEstimate() != !(tc.hasIn && tc.hasOut) {
				t.Fatalf("NeedsTokenEstimate = %v", u.NeedsTokenEstimate())
			}
			if string(resp.Body) != tc.body {
				t.Fatal("usage parsing changed the vendor body")
			}
		})
	}
}
