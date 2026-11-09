package openai

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// responsesOutput is the part of a Responses API response that holds the
// model's answer: its output items, and its status.
type responsesOutput struct {
	Status string `json:"status"`
	Output []struct {
		Type      string `json:"type"`
		CallID    string `json:"call_id"`
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
		Content   []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
	} `json:"output"`
}

// ResponsesAnswer reads a Responses API response body, which /v1/responses
// relays as the provider sent it, into the canonical response the output
// checks read: one choice holding the text of every output_text part of its
// message items, joined, and its function calls as tool calls. The relayed
// body itself is not changed.
func ResponsesAnswer(body []byte) (*models.ChatCompletionResponse, error) {
	var resp responsesOutput
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, err
	}
	return resp.answer(), nil
}

// ResponsesStreamAnswerText is the text of every output_text part of a
// response object, as a Responses stream's response.completed event carries
// it, joined.
func ResponsesStreamAnswerText(response json.RawMessage) string {
	var resp responsesOutput
	if json.Unmarshal(response, &resp) != nil {
		return ""
	}
	return resp.text()
}

func (r *responsesOutput) text() string {
	var text strings.Builder
	for _, item := range r.Output {
		if item.Type != "message" {
			continue
		}
		for _, part := range item.Content {
			if part.Type == "output_text" {
				text.WriteString(part.Text)
			}
		}
	}
	return text.String()
}

func (r *responsesOutput) answer() *models.ChatCompletionResponse {
	msg := models.Message{Role: "assistant"}
	if text := r.text(); text != "" {
		content, _ := json.Marshal(text)
		msg.Content = content
	}
	for _, item := range r.Output {
		if item.Type == "function_call" {
			msg.ToolCalls = append(msg.ToolCalls, models.ToolCall{
				ID: item.CallID, Type: "function",
				Function: models.FunctionCall{Name: item.Name, Arguments: item.Arguments},
			})
		}
	}
	finish := "stop"
	switch {
	case len(msg.ToolCalls) > 0:
		finish = "tool_calls"
	case r.Status == "incomplete":
		finish = "length"
	}
	return &models.ChatCompletionResponse{
		Object:  "chat.completion",
		Choices: []models.Choice{{Index: 0, Message: msg, FinishReason: finish}},
	}
}
