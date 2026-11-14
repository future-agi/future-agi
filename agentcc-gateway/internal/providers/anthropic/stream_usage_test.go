package anthropic

import "testing"

func lastUsageChunk(t *testing.T, lines []string) (prompt, completion, total int) {
	t.Helper()
	chunks, _, err := parseSSELines(lines, newStreamState())
	if err != nil {
		t.Fatalf("parseSSELines: %v", err)
	}
	for _, c := range chunks {
		if c.Usage != nil {
			prompt, completion, total = c.Usage.PromptTokens, c.Usage.CompletionTokens, c.Usage.TotalTokens
		}
	}
	return
}

// Anthropic-compatible providers may report input_tokens as 0 in
// message_start and send the real count in message_delta.
func TestStreamUsage_InputTokensFromMessageDelta(t *testing.T) {
	lines := []string{
		"event: message_start",
		`data: {"type":"message_start","message":{"id":"msg_1","model":"m","usage":{"input_tokens":0,"output_tokens":0}}}`,
		"",
		"event: message_delta",
		`data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":120,"output_tokens":180}}`,
		"",
	}
	p, c, tot := lastUsageChunk(t, lines)
	if p != 120 || c != 180 || tot != 300 {
		t.Fatalf("got prompt=%d completion=%d total=%d, want 120/180/300", p, c, tot)
	}
}

// Native Anthropic: input in message_start, message_delta carries output only.
func TestStreamUsage_InputTokensFromMessageStart(t *testing.T) {
	lines := []string{
		"event: message_start",
		`data: {"type":"message_start","message":{"id":"msg_1","model":"m","usage":{"input_tokens":120,"output_tokens":1}}}`,
		"",
		"event: message_delta",
		`data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":180}}`,
		"",
	}
	p, c, tot := lastUsageChunk(t, lines)
	if p != 120 || c != 180 || tot != 300 {
		t.Fatalf("got prompt=%d completion=%d total=%d, want 120/180/300", p, c, tot)
	}
}
