package anthropic

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AnswerFromNative reads a Messages API response body, which /v1/messages
// relays as the provider sent it, into the canonical response the output
// checks read: its text, tool calls and stop reason, translated as a chat
// request's answer is. The relayed body itself is not changed.
func AnswerFromNative(body []byte) (*models.ChatCompletionResponse, error) {
	var resp anthropicResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, err
	}
	return translateResponse(&resp), nil
}

// FinishReason is a Messages API stop_reason as the canonical finish reason.
func FinishReason(stopReason string) string {
	return mapStopReason(stopReason)
}
