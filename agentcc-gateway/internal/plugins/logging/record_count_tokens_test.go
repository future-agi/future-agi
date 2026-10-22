package logging

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A token count, a Google embedContent, has no chat answer on rc: the
// handler passes the provider's answer through as it is. The handler records
// the status it answered with (metadata response_status) and, for a count,
// the count the provider returned (counted_tokens), and the record logs that
// status.
func TestBuildRecord_TokenCountLogsItsStatusAndCount(t *testing.T) {
	for _, tc := range []struct {
		endpoint, status, counted string
		want                      int
	}{
		{"anthropic_count_tokens", "200", "1234", 200},
		{"genai_count_tokens", "200", "77", 200},
		{"genai_embed", "200", "", 200},
		// The provider refused the count (its 400 passed through).
		{"anthropic_count_tokens", "400", "", 400},
		{"genai_count_tokens", "429", "", 429},
	} {
		t.Run(tc.endpoint+"/"+tc.status, func(t *testing.T) {
			rc := newRC()
			rc.EndpointType = tc.endpoint
			rc.Metadata["response_status"] = tc.status
			if tc.counted != "" {
				rc.Metadata["counted_tokens"] = tc.counted
			}

			rec := buildRecord(rc, enabledCfg())

			if rec.StatusCode != tc.want {
				t.Errorf("StatusCode = %d, want %d", rec.StatusCode, tc.want)
			}
			if tc.counted != "" && rec.Metadata["counted_tokens"] != tc.counted {
				t.Errorf("metadata counted_tokens = %q, want %q", rec.Metadata["counted_tokens"], tc.counted)
			}
			// A count is free at the provider: its tokens are not the
			// request's, and the platform would price them (it prices a
			// log entry's prompt tokens when the gateway gives no cost).
			if rec.PromptTokens != 0 || rec.CompletionTokens != 0 || rec.TotalTokens != 0 {
				t.Errorf("tokens %d/%d/%d, want 0: a count consumes none", rec.PromptTokens, rec.CompletionTokens, rec.TotalTokens)
			}
		})
	}
}

// A count the pipeline refused keeps the refusal's status, and a request of
// another endpoint never takes response_status.
func TestBuildRecord_ResponseStatusIsReadOnlyForPassedThroughAnswers(t *testing.T) {
	rc := newRC()
	rc.EndpointType = "anthropic_count_tokens"
	rc.Errors = []error{&models.APIError{Status: 429, Code: "rate_limit_exceeded", Message: "slow down"}}
	if rec := buildRecord(rc, enabledCfg()); rec.StatusCode != 429 {
		t.Errorf("a refused count logged %d, want 429", rec.StatusCode)
	}

	chat := newRC()
	chat.EndpointType = "chat"
	chat.Metadata["response_status"] = "200"
	if rec := buildRecord(chat, enabledCfg()); rec.StatusCode != 0 {
		t.Errorf("a chat request with no answer logged %d from response_status, want 0", rec.StatusCode)
	}
}
