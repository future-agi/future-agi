package genaifmt

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ValidActions are the supported Google GenAI endpoint actions.
var ValidActions = map[string]bool{
	"generateContent":       true,
	"streamGenerateContent": true,
	"countTokens":           true,
	"embedContent":          true,
}

// ExtractModelFromPath splits "model-name:action" into model and action.
func ExtractModelFromPath(modelAction string) (model string, action string, err error) {
	idx := strings.LastIndex(modelAction, ":")
	if idx < 0 {
		return "", "", fmt.Errorf("malformed path: missing action separator")
	}
	model = modelAction[:idx]
	action = modelAction[idx+1:]
	if model == "" {
		return "", "", fmt.Errorf("model name is empty")
	}
	if !ValidActions[action] {
		return "", "", fmt.Errorf("unsupported action: %s", action)
	}
	return model, action, nil
}

// ExtractTextFromContents extracts text from a Google GenAI request body.
func ExtractTextFromContents(reqBody []byte) string {
	var m struct {
		Contents []struct {
			Parts []json.RawMessage `json:"parts"`
		} `json:"contents"`
		SystemInstruction *struct {
			Parts []json.RawMessage `json:"parts"`
		} `json:"systemInstruction"`
	}
	if err := json.Unmarshal(reqBody, &m); err != nil {
		return ""
	}

	var parts []string

	// System instruction.
	if m.SystemInstruction != nil {
		for _, part := range m.SystemInstruction.Parts {
			if t := extractTextFromPart(part); t != "" {
				parts = append(parts, t)
			}
		}
	}

	// Contents.
	for _, content := range m.Contents {
		for _, part := range content.Parts {
			if t := extractTextFromPart(part); t != "" {
				parts = append(parts, t)
			}
		}
	}

	return strings.Join(parts, " ")
}

// ExtractTextFromResponse extracts text from a Google GenAI response body.
func ExtractTextFromResponse(respBody []byte) string {
	var m struct {
		Candidates []struct {
			Content struct {
				Parts []json.RawMessage `json:"parts"`
			} `json:"content"`
		} `json:"candidates"`
	}
	if err := json.Unmarshal(respBody, &m); err != nil {
		return ""
	}

	var parts []string
	for _, c := range m.Candidates {
		for _, part := range c.Content.Parts {
			if t := extractTextFromPart(part); t != "" {
				parts = append(parts, t)
			}
		}
	}
	return strings.Join(parts, " ")
}

// ExtractUsageMetadata extracts token counts from a Google GenAI response.
func ExtractUsageMetadata(respBody []byte) (promptTokens int, candidateTokens int, totalTokens int) {
	var m struct {
		UsageMetadata struct {
			PromptTokenCount     int `json:"promptTokenCount"`
			CandidatesTokenCount int `json:"candidatesTokenCount"`
			TotalTokenCount      int `json:"totalTokenCount"`
		} `json:"usageMetadata"`
	}
	if err := json.Unmarshal(respBody, &m); err != nil {
		return 0, 0, 0
	}
	return m.UsageMetadata.PromptTokenCount, m.UsageMetadata.CandidatesTokenCount, m.UsageMetadata.TotalTokenCount
}

func extractTextFromPart(raw json.RawMessage) string {
	var part struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &part); err == nil && part.Text != "" {
		return part.Text
	}
	return ""
}
