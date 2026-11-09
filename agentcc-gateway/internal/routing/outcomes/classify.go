package outcomes

import (
	"bytes"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Class is how the gateway classifies a response it served (the log entry's
// metadata outcome_class).
type Class string

const (
	ClassOK            Class = "ok"
	ClassEmpty         Class = "empty"
	ClassContentFilter Class = "content_filter"
)

const (
	finishContentFilter = "content_filter"
	finishToolCalls     = "tool_calls"
)

// Classify sorts a final response into ok, empty or content_filter. The
// content filter comes from finish_reason. A response is empty when it
// generated no completion tokens, made no tool calls and did not stop for
// tool calls; without usage (a stream whose provider sent none), the message
// text stands in for the token count. It reports false when there is no
// evidence either way: no response, or a stream with neither usage nor a
// captured message.
func Classify(resp *models.ChatCompletionResponse) (Class, bool) {
	if resp == nil {
		return "", false
	}
	finish, toolCalls, hasText := "", false, false
	for i, c := range resp.Choices {
		if i == 0 {
			finish = c.FinishReason
		}
		if c.FinishReason == finishContentFilter {
			finish = finishContentFilter
		}
		toolCalls = toolCalls || len(c.Message.ToolCalls) > 0
		hasText = hasText || hasContent(c.Message.Content)
	}
	if finish == finishContentFilter {
		return ClassContentFilter, true
	}
	if toolCalls || finish == finishToolCalls {
		return ClassOK, true
	}
	switch {
	case resp.Usage != nil:
		if resp.Usage.CompletionTokens == 0 {
			return ClassEmpty, true
		}
		return ClassOK, true
	case len(resp.Choices) > 0:
		if !hasText {
			return ClassEmpty, true
		}
		return ClassOK, true
	}
	return "", false
}

// hasContent reports whether a message's raw JSON content says anything: a
// non-empty string, or a non-empty array of parts.
func hasContent(raw []byte) bool {
	raw = bytes.TrimSpace(raw)
	switch {
	case len(raw) == 0, bytes.Equal(raw, []byte("null")), bytes.Equal(raw, []byte(`""`)), bytes.Equal(raw, []byte("[]")):
		return false
	}
	return true
}

// Outcome is the Outcome a classified response records.
func (c Class) Outcome(latencyMs int64) Outcome {
	return Outcome{OK: c == ClassOK, Empty: c == ClassEmpty, ContentFilter: c == ClassContentFilter, LatencyMs: latencyMs}
}
