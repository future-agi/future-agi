package models

import (
	"bytes"
	"encoding/json"
	"maps"
)

// AssistantsRunUsage holds usage data extracted from run completion responses.
type AssistantsRunUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// AssistantsBody is the body of an Assistants API request that may name a
// model or ask for a stream, read as OpenAI reads it: "model" and "stream"
// are the members of exactly that name, the last one where a name comes
// twice (Python's json, as JS's JSON.parse). A null member is absent.
type AssistantsBody struct {
	// Model is the model the body names: an assistant's model, or a run's
	// model override; "" when it names none.
	Model string
	// Stream is whether the body asks for a streamed answer.
	Stream bool

	body    []byte
	members map[string]json.RawMessage // nil for an empty body
}

// ParseAssistantsBody reads body, or refuses it with chat's answer to the
// same body (400 invalid_json; ErrInvalidAssistantsBody): a body that is not
// one JSON object, and a body with a "model" member (in any case, as Go
// reads chat's body) that is not a string, or a "stream" member that is not
// a bool, null aside. So the model the gateway checks is the model OpenAI
// reads. An empty body is {}: it names nothing, and is sent as it came.
func ParseAssistantsBody(body []byte) (*AssistantsBody, error) {
	b := &AssistantsBody{body: body}
	if len(bytes.TrimSpace(body)) == 0 {
		return b, nil
	}
	var typed struct {
		Model  string `json:"model"`
		Stream bool   `json:"stream"`
	}
	if err := json.Unmarshal(body, &typed); err != nil {
		return nil, ErrInvalidAssistantsBody(err.Error())
	}
	if err := json.Unmarshal(body, &b.members); err != nil {
		return nil, ErrInvalidAssistantsBody(err.Error())
	}
	if b.members == nil {
		return nil, ErrInvalidAssistantsBody("the request body must be a JSON object")
	}
	// Each member below decoded into its field's type above, so these
	// decodes cannot fail; null leaves the field as it is, absent.
	if m, ok := b.members["model"]; ok {
		_ = json.Unmarshal(m, &b.Model)
	}
	if v, ok := b.members["stream"]; ok {
		_ = json.Unmarshal(v, &b.Stream)
	}
	return b, nil
}

// ErrInvalidAssistantsBody is chat's answer to a body it cannot read.
func ErrInvalidAssistantsBody(reason string) *APIError {
	return ErrBadRequest("invalid_json", "Invalid JSON in request body: "+reason)
}

// WithModel is the body to send, with model as its "model": a body with a
// "model" member is written again from its members, so the provider gets
// one "model", the one the gateway checked (model; the member as it came
// when model is "", the body naming none). A body with no "model" member is
// sent as it came.
func (b *AssistantsBody) WithModel(model string) ([]byte, error) {
	if _, ok := b.members["model"]; !ok {
		return b.body, nil
	}
	members := maps.Clone(b.members)
	if model != "" {
		m, err := json.Marshal(model)
		if err != nil {
			return nil, err
		}
		members["model"] = m
	}
	return json.Marshal(members)
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
