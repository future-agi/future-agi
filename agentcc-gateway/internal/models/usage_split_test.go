package models

import (
	"encoding/json"
	"testing"
)

func TestStreamChunkUsage_InputOutputAliases(t *testing.T) {
	raw := `{"id":"c1","object":"chat.completion.chunk","choices":[],` +
		`"usage":{"input_tokens":120,"output_tokens":180,"total_tokens":300}}`
	var chunk StreamChunk
	if err := json.Unmarshal([]byte(raw), &chunk); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if chunk.Usage == nil {
		t.Fatal("usage is nil")
	}
	if chunk.Usage.PromptTokens != 120 || chunk.Usage.CompletionTokens != 180 || chunk.Usage.TotalTokens != 300 {
		t.Fatalf("got %+v, want prompt=120 completion=180 total=300", *chunk.Usage)
	}
}

func TestUsage_OpenAIFieldsUnchanged(t *testing.T) {
	raw := `{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15,"prompt_tokens_details":{"cached_tokens":2}}`
	var u Usage
	if err := json.Unmarshal([]byte(raw), &u); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if u.PromptTokens != 10 || u.CompletionTokens != 5 || u.TotalTokens != 15 || u.PromptTokensDetails == nil {
		t.Fatalf("got %+v", u)
	}
}

func TestUsage_AliasPrecedence(t *testing.T) {
	for _, tc := range []struct {
		name string
		raw  string
		want Usage
	}{
		{"canonical zero wins", `{"prompt_tokens":0,"completion_tokens":0,"input_tokens":7,"output_tokens":3}`, Usage{}},
		{"canonical nonzero wins", `{"prompt_tokens":2,"completion_tokens":1,"input_tokens":7,"output_tokens":3}`, Usage{PromptTokens: 2, CompletionTokens: 1, TotalTokens: 3}},
		{"mixed spellings", `{"prompt_tokens":0,"input_tokens":7,"output_tokens":3}`, Usage{CompletionTokens: 3, TotalTokens: 3}},
		{"null canonical falls back", `{"prompt_tokens":null,"completion_tokens":null,"input_tokens":7,"output_tokens":3}`, Usage{PromptTokens: 7, CompletionTokens: 3, TotalTokens: 10}},
		{"zero total derived", `{"input_tokens":7,"output_tokens":3,"total_tokens":0}`, Usage{PromptTokens: 7, CompletionTokens: 3, TotalTokens: 10}},
		{"nonzero total preserved", `{"input_tokens":7,"output_tokens":3,"total_tokens":12}`, Usage{PromptTokens: 7, CompletionTokens: 3, TotalTokens: 12}},
		{"empty usage", `{}`, Usage{}},
		{"null usage", `null`, Usage{}},
		{"null aliases", `{"input_tokens":null,"output_tokens":null}`, Usage{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var got Usage
			if err := json.Unmarshal([]byte(tc.raw), &got); err != nil {
				t.Fatal(err)
			}
			if got.PromptTokens != tc.want.PromptTokens || got.CompletionTokens != tc.want.CompletionTokens || got.TotalTokens != tc.want.TotalTokens {
				t.Fatalf("got %d/%d/%d, want %d/%d/%d", got.PromptTokens, got.CompletionTokens, got.TotalTokens, tc.want.PromptTokens, tc.want.CompletionTokens, tc.want.TotalTokens)
			}
		})
	}
}

func TestUsage_AliasesPreserveDetailsAndExtras(t *testing.T) {
	raw := `{"input_tokens":7,"output_tokens":3,"prompt_tokens_details":{"cached_tokens":2},"completion_tokens_details":{"reasoning_tokens":1},"server_tool_use":{"web_search_requests":4},"citation_tokens":6}`
	var u Usage
	if err := json.Unmarshal([]byte(raw), &u); err != nil {
		t.Fatal(err)
	}
	if u.PromptTokens != 7 || u.CompletionTokens != 3 || u.TotalTokens != 10 {
		t.Fatalf("got token split %d/%d/%d, want 7/3/10", u.PromptTokens, u.CompletionTokens, u.TotalTokens)
	}
	if u.PromptTokensDetails == nil || string(*u.PromptTokensDetails) != `{"cached_tokens":2}` ||
		u.CompletionTokensDetails == nil || string(*u.CompletionTokensDetails) != `{"reasoning_tokens":1}` ||
		u.ServerToolUse == nil || u.ServerToolUse.WebSearchRequests != 4 {
		t.Fatalf("usage details lost: %+v", u)
	}
	encoded, err := json.Marshal(u)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]string{"input_tokens": "7", "output_tokens": "3", "citation_tokens": "6", "prompt_tokens": "7", "completion_tokens": "3", "total_tokens": "10"} {
		if string(fields[key]) != want {
			t.Errorf("round trip %s = %s, want %s", key, fields[key], want)
		}
	}
}

func TestUsage_InvalidAliases(t *testing.T) {
	for _, raw := range []string{`{"input_tokens":"7"}`, `{"output_tokens":1.5}`, `{"input_tokens":9223372036854775808}`, `{"output_tokens":{}}`, `{"input_tokens":`} {
		var u Usage
		if err := json.Unmarshal([]byte(raw), &u); err == nil {
			t.Errorf("accepted invalid usage %s", raw)
		}
	}
}

func TestUsage_TotalDerivedWhenMissing(t *testing.T) {
	var u Usage
	if err := json.Unmarshal([]byte(`{"input_tokens":7,"output_tokens":3}`), &u); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if u.TotalTokens != 10 {
		t.Fatalf("total = %d, want 10", u.TotalTokens)
	}
}
