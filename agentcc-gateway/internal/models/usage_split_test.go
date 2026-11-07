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

func TestUsage_TotalDerivedWhenMissing(t *testing.T) {
	var u Usage
	if err := json.Unmarshal([]byte(`{"input_tokens":7,"output_tokens":3}`), &u); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if u.TotalTokens != 10 {
		t.Fatalf("total = %d, want 10", u.TotalTokens)
	}
}
