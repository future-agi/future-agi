package anthropicfmt

import "encoding/json"

// ExtractMinimalRequest extracts model, stream flag, and max_tokens presence from an Anthropic request body.
func ExtractMinimalRequest(reqBody []byte) (model string, stream bool, hasMaxTokens bool, err error) {
	var m struct {
		Model     string           `json:"model"`
		Stream    bool             `json:"stream"`
		MaxTokens *json.RawMessage `json:"max_tokens"`
	}
	if err := json.Unmarshal(reqBody, &m); err != nil {
		return "", false, false, err
	}
	return m.Model, m.Stream, m.MaxTokens != nil, nil
}

// ExtractTextFromMessages extracts text content from an Anthropic messages request for guardrail inspection.
func ExtractTextFromMessages(reqBody []byte) string {
	var m struct {
		System   json.RawMessage `json:"system"`
		Messages []struct {
			Role    string          `json:"role"`
			Content json.RawMessage `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(reqBody, &m); err != nil {
		return ""
	}

	var parts []string

	// Extract system text.
	if m.System != nil {
		parts = append(parts, extractAnthropicContent(m.System)...)
	}

	// Extract message text.
	for _, msg := range m.Messages {
		if msg.Content != nil {
			parts = append(parts, extractAnthropicContent(msg.Content)...)
		}
	}

	return joinParts(parts)
}

// ExtractTextFromResponse extracts text from an Anthropic messages response.
func ExtractTextFromResponse(respBody []byte) string {
	var m struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
	}
	if err := json.Unmarshal(respBody, &m); err != nil {
		return ""
	}
	var parts []string
	for _, c := range m.Content {
		if c.Type == "text" && c.Text != "" {
			parts = append(parts, c.Text)
		}
	}
	return joinParts(parts)
}

// ExtractUsage extracts input and output token counts from an Anthropic response.
func ExtractUsage(respBody []byte) (inputTokens int, outputTokens int) {
	var m struct {
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(respBody, &m); err != nil {
		return 0, 0
	}
	return m.Usage.InputTokens, m.Usage.OutputTokens
}

// ExtractModel extracts the model name from an Anthropic response body.
func ExtractModel(respBody []byte) string {
	var m struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(respBody, &m); err != nil {
		return ""
	}
	return m.Model
}

// extractAnthropicContent handles content that can be a string or array of content blocks.
func extractAnthropicContent(raw json.RawMessage) []string {
	// Try as string first.
	var s string
	if err := json.Unmarshal(raw, &s); err == nil && s != "" {
		return []string{s}
	}

	// Try as array of content blocks.
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &blocks); err == nil {
		var parts []string
		for _, b := range blocks {
			if b.Type == "text" && b.Text != "" {
				parts = append(parts, b.Text)
			}
		}
		return parts
	}

	return nil
}

func joinParts(parts []string) string {
	if len(parts) == 0 {
		return ""
	}
	result := parts[0]
	for _, p := range parts[1:] {
		result += " " + p
	}
	return result
}
