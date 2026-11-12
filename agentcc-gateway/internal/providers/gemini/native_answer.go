package gemini

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AnswerFromNative reads a generateContent response body, which /v1beta
// relays as the provider sent it, into the canonical response the output
// checks read: one choice per candidate, each translated as a chat request's
// answer is (its text, tool calls and finish reason). Every candidate counts,
// since the client gets every one. The relayed body itself is not changed.
func AnswerFromNative(body []byte) (*models.ChatCompletionResponse, error) {
	var resp geminiResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, err
	}
	answer := &models.ChatCompletionResponse{Object: "chat.completion", Model: resp.ModelVersion}
	for i, candidate := range resp.Candidates {
		one := translateResponse(&geminiResponse{Candidates: []geminiCandidate{candidate}}, resp.ModelVersion)
		for _, choice := range one.Choices {
			choice.Index = i
			answer.Choices = append(answer.Choices, choice)
		}
	}
	return answer, nil
}

// FinishReason is a generateContent finishReason as the canonical finish
// reason.
func FinishReason(reason string) string {
	return mapGeminiFinishReason(reason)
}
