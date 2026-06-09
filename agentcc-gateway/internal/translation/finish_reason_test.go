package translation_test

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/translation"
)

func TestMapFinishReason(t *testing.T) {
	tests := []struct {
		format string
		native string
		want   string
	}{
		// Anthropic mappings.
		{"anthropic", "end_turn", "stop"},
		{"anthropic", "max_tokens", "length"},
		{"anthropic", "tool_use", "tool_calls"},
		{"anthropic", "stop_sequence", "stop"},
		{"anthropic", "unknown_anthropic_reason", ""},

		// Google / Gemini mappings.
		{"google", "STOP", "stop"},
		{"google", "MAX_TOKENS", "length"},
		{"google", "SAFETY", "content_filter"},
		{"google", "RECITATION", "content_filter"},
		{"google", "BLOCKLIST", "content_filter"},
		{"google", "PROHIBITED_CONTENT", "content_filter"},
		{"google", "SPII", "content_filter"},
		{"google", "MALFORMED_FUNCTION_CALL", "stop"},
		{"google", "FINISH_REASON_UNSPECIFIED", "stop"},
		{"google", "OTHER", "stop"},
		{"google", "UNKNOWN_GEMINI_REASON", ""},

		// Cohere mappings (future use).
		{"cohere", "COMPLETE", "stop"},
		{"cohere", "MAX_TOKENS", "length"},
		{"cohere", "TOOL_CALL", "tool_calls"},
		{"cohere", "ERROR", "stop"},
		{"cohere", "UNKNOWN_COHERE_REASON", ""},

		// Unknown format always returns "".
		{"unknown-format", "stop", ""},
		{"", "stop", ""},
	}

	for _, tc := range tests {
		t.Run(tc.format+"/"+tc.native, func(t *testing.T) {
			got := translation.MapFinishReason(tc.format, tc.native)
			if got != tc.want {
				t.Errorf("MapFinishReason(%q, %q) = %q, want %q", tc.format, tc.native, got, tc.want)
			}
		})
	}
}

func TestMapFinishReasonUnmappedReturnsEmpty(t *testing.T) {
	// Callers are expected to default to "stop" when they get "".
	got := translation.MapFinishReason("anthropic", "totally_made_up")
	if got != "" {
		t.Errorf("expected empty string for unmapped reason, got %q", got)
	}
}
