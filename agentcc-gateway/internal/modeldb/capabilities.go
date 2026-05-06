package modeldb

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CapabilityFlags tracks what features a model supports.
type CapabilityFlags struct {
	FunctionCalling   bool `json:"function_calling"`
	ParallelToolCalls bool `json:"parallel_tool_calls"`
	Vision            bool `json:"vision"`
	AudioInput        bool `json:"audio_input"`
	AudioOutput       bool `json:"audio_output"`
	PDFInput          bool `json:"pdf_input"`
	Streaming         bool `json:"streaming"`
	ResponseSchema    bool `json:"response_schema"`
	SystemMessages    bool `json:"system_messages"`
	PromptCaching     bool `json:"prompt_caching"`
	Reasoning         bool `json:"reasoning"`
}

// ValidateRequest checks if the model supports the features used in the request.
// Returns ("", true) if OK, or (reason, false) if a capability is missing.
func (c *CapabilityFlags) ValidateRequest(req *models.ChatCompletionRequest) (string, bool) {
	// Check tool/function calling.
	if len(req.Tools) > 0 && !c.FunctionCalling {
		return "model does not support function calling / tools", false
	}

	// Check vision (image content in messages).
	if hasImageContent(req.Messages) && !c.Vision {
		return "model does not support vision / image input", false
	}

	// Check structured output / JSON schema.
	if req.ResponseFormat != nil && req.ResponseFormat.Type == "json_schema" && !c.ResponseSchema {
		return "model does not support structured output (json_schema response format)", false
	}

	return "", true
}

// hasImageContent checks if any message contains image_url content parts.
func hasImageContent(messages []models.Message) bool {
	for _, msg := range messages {
		if msg.Content == nil {
			continue
		}
		// Content can be a string or an array of content parts.
		// If it's an array, check for image_url type parts.
		var parts []struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(msg.Content, &parts); err == nil {
			for _, p := range parts {
				if p.Type == "image_url" || p.Type == "image" {
					return true
				}
			}
		}
	}
	return false
}
