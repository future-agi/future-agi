package anthropic

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// checkCacheUsage checks an Anthropic usage mapped with 10 uncached input,
// 40 cache-read, 30 cache-write and 5 output tokens. Prompt tokens count
// every input token, cached ones included, as OpenAI's prompt_tokens does.
func checkCacheUsage(t *testing.T, u *models.Usage) {
	t.Helper()
	if u == nil {
		t.Fatal("no usage")
	}
	if u.PromptTokens != 80 || u.CompletionTokens != 5 || u.TotalTokens != 85 {
		t.Errorf("tokens = prompt %d completion %d total %d, want 80/5/85", u.PromptTokens, u.CompletionTokens, u.TotalTokens)
	}
	if u.PromptDetails.CachedTokens != 40 || u.PromptDetails.CacheWriteTokens != 30 {
		t.Errorf("sidecars = %+v, want cached 40 and cache-write 30", u.PromptDetails)
	}
	if u.PromptTokensDetails == nil {
		t.Fatal("prompt_tokens_details not written")
	}
	var raw map[string]int
	if err := json.Unmarshal(*u.PromptTokensDetails, &raw); err != nil || raw["cached_tokens"] != 40 || raw["cache_write_tokens"] != 30 {
		t.Errorf("prompt_tokens_details = %s (%v), want cached_tokens 40 and cache_write_tokens 30", *u.PromptTokensDetails, err)
	}
	// Reparsing the raw details gives the same sidecars, as the request path does.
	again := *u
	again.PromptDetails = models.PromptTokensDetails{}
	again.ParseDetails()
	if again.PromptDetails != u.PromptDetails {
		t.Errorf("reparsed sidecars %+v differ from %+v", again.PromptDetails, u.PromptDetails)
	}
}

func TestTranslateResponseCacheTokens(t *testing.T) {
	var resp anthropicResponse
	body := `{"id":"msg_c","type":"message","model":"claude-sonnet-4-5","role":"assistant",
		"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",
		"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":30,"output_tokens":5}}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	checkCacheUsage(t, translateResponse(&resp).Usage)
}

func TestTranslateResponseNoCacheTokensUnchanged(t *testing.T) {
	u := translateResponse(&anthropicResponse{Content: []anthropicContentBlock{{Type: "text", Text: "ok"}},
		Usage: anthropicUsage{InputTokens: 100, OutputTokens: 50}}).Usage
	if u.PromptTokens != 100 || u.TotalTokens != 150 || u.PromptTokensDetails != nil {
		t.Fatalf("a response without cache tokens became %+v (details %v), want it unchanged", u, u.PromptTokensDetails)
	}
}

func TestStreamCacheTokens(t *testing.T) {
	for _, tc := range []struct {
		name  string
		delta string
	}{
		// message_start carries the input side; message_delta only the output.
		{"input on message_start", `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}`},
		// Newer streams repeat the cumulative input counts on message_delta.
		{"input repeated on message_delta", `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":30,"output_tokens":5}}`},
		// A delta repeating only some counts keeps message_start's others.
		{"input partly repeated on message_delta", `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":10,"output_tokens":5}}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			state := newStreamState()
			start := `{"type":"message_start","message":{"id":"msg_s","model":"claude-sonnet-4-5",` +
				`"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":30,"output_tokens":1}}}`
			if _, _, err := state.parseSSELine("message_start", start); err != nil {
				t.Fatal(err)
			}
			chunk, _, err := state.parseSSELine("message_delta", tc.delta)
			if err != nil || chunk == nil {
				t.Fatalf("message_delta: chunk %v, err %v", chunk, err)
			}
			checkCacheUsage(t, chunk.Usage)
		})
	}
}
