package bedrock

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// checkCacheUsage checks a Bedrock Anthropic usage mapped with 10 uncached
// input, 40 cache-read, 30 cache-write and 5 output tokens.
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
	var raw map[string]int
	if u.PromptTokensDetails == nil || json.Unmarshal(*u.PromptTokensDetails, &raw) != nil ||
		raw["cached_tokens"] != 40 || raw["cache_write_tokens"] != 30 {
		t.Errorf("prompt_tokens_details = %v, want cached_tokens 40 and cache_write_tokens 30", u.PromptTokensDetails)
	}
}

func TestBedrockAnthropicResponseCacheTokens(t *testing.T) {
	var resp bedrockResponse
	body := `{"id":"msg_b","type":"message","model":"anthropic.claude","role":"assistant",
		"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",
		"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":30,"output_tokens":5}}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	checkCacheUsage(t, translateResponse(&resp).Usage)
}

func TestBedrockAnthropicStreamCacheTokens(t *testing.T) {
	event := func(payload string) *eventStreamMessage {
		return &eventStreamMessage{Headers: map[string]string{":message-type": "event"}, Payload: []byte(payload)}
	}
	state := newBedrockStreamState("anthropic.claude")
	start := `{"type":"message_start","message":{"id":"msg_bs","model":"anthropic.claude",` +
		`"usage":{"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":30,"output_tokens":1}}}`
	if _, _, err := state.parseStreamPayload(event(start)); err != nil {
		t.Fatal(err)
	}
	chunk, _, err := state.parseStreamPayload(event(`{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}`))
	if err != nil || chunk == nil {
		t.Fatalf("message_delta: chunk %v, err %v", chunk, err)
	}
	checkCacheUsage(t, chunk.Usage)
}
