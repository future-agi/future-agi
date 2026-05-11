package models

import "encoding/json"

// AssistantsRunUsage holds usage data extracted from run completion responses.
type AssistantsRunUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// ExtractModelFromBody parses a JSON body just enough to extract the "model" field.
// Returns empty string if no model field is present or on parse error.
func ExtractModelFromBody(body []byte) string {
	var m struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return ""
	}
	return m.Model
}

// ReplaceModelInBody replaces the "model" field in a JSON body with a new value.
// Returns the original body unchanged if there is no model field.
func ReplaceModelInBody(body []byte, newModel string) ([]byte, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, err
	}
	if _, ok := raw["model"]; !ok {
		return body, nil
	}
	modelJSON, err := json.Marshal(newModel)
	if err != nil {
		return nil, err
	}
	raw["model"] = modelJSON
	return json.Marshal(raw)
}

// ExtractStreamFromBody parses the "stream" field from a JSON body.
// Returns false if not present or on parse error.
func ExtractStreamFromBody(body []byte) bool {
	var m struct {
		Stream bool `json:"stream"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return false
	}
	return m.Stream
}

// ExtractRunUsage parses the "usage" object from a run response body.
// Returns nil if no usage field is present or run is not completed.
func ExtractRunUsage(body []byte) *AssistantsRunUsage {
	var m struct {
		Usage *AssistantsRunUsage `json:"usage"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return nil
	}
	return m.Usage
}

// ExtractMessageContent extracts text content from a Create Message request body.
// Content can be a string or an array of content parts.
func ExtractMessageContent(body []byte) string {
	var m struct {
		Content json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(body, &m); err != nil || m.Content == nil {
		return ""
	}
	return extractContentFromRaw(m.Content)
}

// ExtractThreadMessagesContent extracts text from initial messages in a Create Thread request.
func ExtractThreadMessagesContent(body []byte) string {
	var m struct {
		Messages []struct {
			Content json.RawMessage `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return ""
	}
	var result string
	for _, msg := range m.Messages {
		if msg.Content != nil {
			if text := extractContentFromRaw(msg.Content); text != "" {
				if result != "" {
					result += "\n"
				}
				result += text
			}
		}
	}
	return result
}

// ExtractRunAdditionalMessagesContent extracts text from additional_messages in a Create Run request.
func ExtractRunAdditionalMessagesContent(body []byte) string {
	var m struct {
		AdditionalMessages []struct {
			Content json.RawMessage `json:"content"`
		} `json:"additional_messages"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return ""
	}
	var result string
	for _, msg := range m.AdditionalMessages {
		if msg.Content != nil {
			if text := extractContentFromRaw(msg.Content); text != "" {
				if result != "" {
					result += "\n"
				}
				result += text
			}
		}
	}
	return result
}

// ExtractThreadAndRunMessagesContent extracts text from thread.messages in a Create Thread-and-Run request.
func ExtractThreadAndRunMessagesContent(body []byte) string {
	var m struct {
		Thread struct {
			Messages []struct {
				Content json.RawMessage `json:"content"`
			} `json:"messages"`
		} `json:"thread"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return ""
	}
	var result string
	for _, msg := range m.Thread.Messages {
		if msg.Content != nil {
			if text := extractContentFromRaw(msg.Content); text != "" {
				if result != "" {
					result += "\n"
				}
				result += text
			}
		}
	}
	return result
}

// extractContentFromRaw handles content that can be a string or array of content parts.
func extractContentFromRaw(raw json.RawMessage) string {
	// Try as string first.
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}

	// Try as array of content parts.
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var result string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				if result != "" {
					result += "\n"
				}
				result += p.Text
			}
		}
		return result
	}

	return ""
}
