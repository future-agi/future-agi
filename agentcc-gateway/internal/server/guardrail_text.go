package server

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// The routes that forward the client's own body leave rc.Request nil and
// rc.Response holding usage only, so a guardrail would check nothing. These
// give the guardrails the text of the request and the response instead, in
// rc.GuardrailRequest and rc.GuardrailResponse.

// guardrailRequest is the text of a native request body, read by the inbound
// translator for its dialect ("anthropic", "google"). A body the translator
// cannot read gives nil, and the upstream answers the malformed body as before.
func guardrailRequest(dialect string, body []byte) *models.ChatCompletionRequest {
	t, ok := translation.InboundFor(dialect)
	if !ok {
		return nil
	}
	req, _, err := t.RequestToCanonical(body)
	if err != nil {
		return nil
	}
	return req
}

// responsesGuardrailRequest is the text of a Responses request: its
// instructions, then each input item, a plain string input included.
func responsesGuardrailRequest(req *models.ResponsesRequest) *models.ChatCompletionRequest {
	out := &models.ChatCompletionRequest{Model: req.Model}
	if req.Instructions != "" {
		out.Messages = append(out.Messages, textMessage("system", req.Instructions))
	}
	var input string
	if json.Unmarshal(req.Input, &input) == nil {
		if input != "" {
			out.Messages = append(out.Messages, textMessage("user", input))
		}
		return out
	}
	var items []struct {
		Type      string          `json:"type"`
		Role      string          `json:"role"`
		Content   json.RawMessage `json:"content"`
		Output    json.RawMessage `json:"output"`
		Arguments string          `json:"arguments"`
	}
	if json.Unmarshal(req.Input, &items) != nil {
		return out
	}
	for _, item := range items {
		role, text := item.Role, contentText(item.Content)
		switch item.Type {
		case "function_call_output":
			role, text = "tool", contentText(item.Output)
		case "function_call":
			role, text = "assistant", item.Arguments
		}
		if role == "" {
			role = "user"
		}
		if text != "" {
			out.Messages = append(out.Messages, textMessage(role, text))
		}
	}
	return out
}

// contentText is the text of a content value: a string, or parts that carry
// a "text" field (input_text, output_text, text).
func contentText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s
	}
	var parts []struct {
		Text string `json:"text"`
	}
	if json.Unmarshal(raw, &parts) != nil {
		return ""
	}
	texts := make([]string, 0, len(parts))
	for _, p := range parts {
		if p.Text != "" {
			texts = append(texts, p.Text)
		}
	}
	return strings.Join(texts, "\n")
}

func textMessage(role, text string) models.Message {
	content, _ := json.Marshal(text)
	return models.Message{Role: role, Content: content}
}

// assistantText is a response's text in the shape the output guardrails read.
func assistantText(text string) *models.ChatCompletionResponse {
	if text == "" {
		return nil
	}
	return &models.ChatCompletionResponse{Choices: []models.Choice{{Message: textMessage("assistant", text)}}}
}

// anthropicResponseText is the text blocks of a Messages API response.
func anthropicResponseText(body []byte) string {
	var resp struct {
		Content json.RawMessage `json:"content"`
	}
	if json.Unmarshal(body, &resp) != nil {
		return ""
	}
	return contentText(resp.Content)
}

// responsesOutputText is the text of each message item in a Responses API
// response.
func responsesOutputText(body []byte) string {
	var resp struct {
		Output []struct {
			Type    string          `json:"type"`
			Content json.RawMessage `json:"content"`
		} `json:"output"`
	}
	if json.Unmarshal(body, &resp) != nil {
		return ""
	}
	var texts []string
	for _, item := range resp.Output {
		if item.Type == "message" {
			if t := contentText(item.Content); t != "" {
				texts = append(texts, t)
			}
		}
	}
	return strings.Join(texts, "\n")
}

// genaiResponseText is the text parts of each candidate in a GenAI response.
func genaiResponseText(body []byte) string {
	var resp struct {
		Candidates []struct {
			Content struct {
				Parts json.RawMessage `json:"parts"`
			} `json:"content"`
		} `json:"candidates"`
	}
	if json.Unmarshal(body, &resp) != nil {
		return ""
	}
	var texts []string
	for _, c := range resp.Candidates {
		if t := contentText(c.Content.Parts); t != "" {
			texts = append(texts, t)
		}
	}
	return strings.Join(texts, "\n")
}
