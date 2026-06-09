package anthropic

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- Anthropic native types ---

type anthropicRequest struct {
	Model         string                 `json:"model"`
	Messages      []anthropicMessage     `json:"messages"`
	System        string                 `json:"system,omitempty"`
	MaxTokens     int                    `json:"max_tokens"`
	Temperature   *float64               `json:"temperature,omitempty"`
	TopP          *float64               `json:"top_p,omitempty"`
	StopSequences []string               `json:"stop_sequences,omitempty"`
	Stream        bool                   `json:"stream,omitempty"`
	Tools         []anthropicTool        `json:"tools,omitempty"`
	ToolChoice    *anthropicToolChoice   `json:"tool_choice,omitempty"`
	OutputConfig  *anthropicOutputConfig `json:"output_config,omitempty"`
}

type anthropicOutputConfig struct {
	Format *anthropicOutputFormat `json:"format,omitempty"`
}

type anthropicOutputFormat struct {
	Type   string          `json:"type"`
	Schema json.RawMessage `json:"schema,omitempty"`
}

type anthropicMessage struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

type anthropicContentBlock struct {
	Type      string          `json:"type"`
	Text      string          `json:"text,omitempty"`
	ID        string          `json:"id,omitempty"`
	Name      string          `json:"name,omitempty"`
	Input     json.RawMessage `json:"input,omitempty"`
	ToolUseID string          `json:"tool_use_id,omitempty"`
	Content   json.RawMessage `json:"content,omitempty"`
	Source    *imageSource    `json:"source,omitempty"`
	Thinking  string          `json:"thinking,omitempty"`
}

type imageSource struct {
	Type      string `json:"type"`
	MediaType string `json:"media_type,omitempty"`
	Data      string `json:"data,omitempty"`
	URL       string `json:"url,omitempty"`
}

type anthropicTool struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	InputSchema json.RawMessage `json:"input_schema"`
}

type anthropicToolChoice struct {
	Type string `json:"type"`
	Name string `json:"name,omitempty"`
}

type anthropicResponse struct {
	ID         string                  `json:"id"`
	Type       string                  `json:"type"`
	Model      string                  `json:"model"`
	Role       string                  `json:"role"`
	Content    []anthropicContentBlock `json:"content"`
	StopReason string                  `json:"stop_reason"`
	Usage      anthropicUsage          `json:"usage"`
}

type anthropicUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

type anthropicErrorResponse struct {
	Type  string               `json:"type"`
	Error anthropicErrorDetail `json:"error"`
}

type anthropicErrorDetail struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// --- Translation functions ---

func translateRequest(req *models.ChatCompletionRequest) (*anthropicRequest, error) {
	ar := &anthropicRequest{
		Model:       resolveModelName(req.Model),
		Temperature: req.Temperature,
		TopP:        req.TopP,
		Stream:      req.Stream,
	}

	// Set max_tokens (required by Anthropic).
	switch {
	case req.MaxTokens != nil:
		ar.MaxTokens = *req.MaxTokens
	case req.MaxCompletionTokens != nil:
		ar.MaxTokens = *req.MaxCompletionTokens
	default:
		ar.MaxTokens = 4096
	}

	// Parse stop sequences.
	if len(req.Stop) > 0 {
		var stops []string
		// Try as array of strings first.
		if err := json.Unmarshal(req.Stop, &stops); err != nil {
			// Try as single string.
			var single string
			if err := json.Unmarshal(req.Stop, &single); err == nil {
				stops = []string{single}
			}
		}
		ar.StopSequences = stops
	}

	// Extract system messages and convert remaining messages.
	var systemParts []string
	for _, msg := range req.Messages {
		if msg.Role == "system" {
			text := extractTextContent(msg.Content)
			if text != "" {
				systemParts = append(systemParts, text)
			}
			continue
		}

		am, err := translateMessage(msg)
		if err != nil {
			return nil, fmt.Errorf("translating message: %w", err)
		}
		ar.Messages = append(ar.Messages, am)
	}

	if len(systemParts) > 0 {
		system := systemParts[0]
		for i := 1; i < len(systemParts); i++ {
			system += "\n\n" + systemParts[i]
		}
		ar.System = system
	}

	// Translate tools.
	if len(req.Tools) > 0 {
		for _, t := range req.Tools {
			if t.Type != "function" {
				continue
			}
			ar.Tools = append(ar.Tools, anthropicTool{
				Name:        t.Function.Name,
				Description: t.Function.Description,
				InputSchema: t.Function.Parameters,
			})
		}
	}

	// Translate tool_choice.
	if len(req.ToolChoice) > 0 {
		tc, err := translateToolChoice(req.ToolChoice)
		if err == nil && tc != nil {
			ar.ToolChoice = tc
		}
	}

	if schema := extractResponseFormatSchema(req.ResponseFormat); len(schema) > 0 {
		ar.OutputConfig = &anthropicOutputConfig{
			Format: &anthropicOutputFormat{
				Type:   "json_schema",
				Schema: schema,
			},
		}
	}

	return ar, nil
}

func extractResponseFormatSchema(rf *models.ResponseFormat) json.RawMessage {
	if rf == nil || rf.Type != "json_schema" || len(rf.JSONSchema) == 0 {
		return nil
	}
	var wrapper struct {
		Schema json.RawMessage `json:"schema"`
	}
	if json.Unmarshal(rf.JSONSchema, &wrapper) == nil && len(wrapper.Schema) > 0 {
		return wrapper.Schema
	}
	return rf.JSONSchema
}

func translateMessage(msg models.Message) (anthropicMessage, error) {
	am := anthropicMessage{
		Role: msg.Role,
	}

	// Handle tool result messages.
	if msg.Role == "tool" {
		am.Role = "user"
		block := anthropicContentBlock{
			Type:      "tool_result",
			ToolUseID: msg.ToolCallID,
		}
		text := extractTextContent(msg.Content)
		if text != "" {
			block.Content = json.RawMessage(fmt.Sprintf(`[{"type":"text","text":%s}]`, mustMarshal(text)))
		}
		content, _ := json.Marshal([]anthropicContentBlock{block})
		am.Content = content
		return am, nil
	}

	// Handle assistant messages with tool calls.
	if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
		var blocks []anthropicContentBlock

		// Add text content if present.
		text := extractTextContent(msg.Content)
		if text != "" {
			blocks = append(blocks, anthropicContentBlock{
				Type: "text",
				Text: text,
			})
		}

		// Add tool use blocks.
		for _, tc := range msg.ToolCalls {
			blocks = append(blocks, anthropicContentBlock{
				Type:  "tool_use",
				ID:    tc.ID,
				Name:  tc.Function.Name,
				Input: json.RawMessage(tc.Function.Arguments),
			})
		}

		content, _ := json.Marshal(blocks)
		am.Content = content
		return am, nil
	}

	// Standard message: check for vision content first (handles both text + images),
	// then fall back to text-only, then pass-through.
	if blocks := translateVisionContent(msg.Content); blocks != nil {
		content, _ := json.Marshal(blocks)
		am.Content = content
	} else if text := extractTextContent(msg.Content); text != "" {
		blocks := []anthropicContentBlock{{Type: "text", Text: text}}
		content, _ := json.Marshal(blocks)
		am.Content = content
	} else {
		// Content might already be structured — pass through as-is.
		am.Content = msg.Content
	}

	return am, nil
}

// translateVisionContent converts OpenAI vision content parts to Anthropic format.
// OpenAI: [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]
// OpenAI: [{"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}]
// OpenAI: [{"type":"file","file":{"file_id":"https://example.com/video.mp4","format":"video/mp4"}}]
// Anthropic: [{"type":"text","text":"..."}, {"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}]
func translateVisionContent(content json.RawMessage) []anthropicContentBlock {
	if len(content) == 0 {
		return nil
	}

	var parts []struct {
		Type     string `json:"type"`
		Text     string `json:"text"`
		ImageURL *struct {
			URL    string `json:"url"`
			Detail string `json:"detail,omitempty"`
		} `json:"image_url"`
		InputAudio *struct {
			Data   string `json:"data"`
			Format string `json:"format"`
		} `json:"input_audio"`
		File *struct {
			FileID string `json:"file_id"`
			Format string `json:"format"`
		} `json:"file"`
	}
	if err := json.Unmarshal(content, &parts); err != nil {
		return nil
	}

	// Only process if we find multimodal content parts (image_url, input_audio, or file).
	hasMultimodal := false
	for _, p := range parts {
		if (p.Type == "image_url" && p.ImageURL != nil) ||
			(p.Type == "input_audio" && p.InputAudio != nil) ||
			(p.Type == "file" && p.File != nil) {
			hasMultimodal = true
			break
		}
	}
	if !hasMultimodal {
		return nil
	}

	var blocks []anthropicContentBlock
	for _, p := range parts {
		switch p.Type {
		case "text":
			blocks = append(blocks, anthropicContentBlock{Type: "text", Text: p.Text})
		case "image_url":
			if p.ImageURL == nil {
				continue
			}
			block := convertImageToAnthropic(p.ImageURL.URL)
			if block != nil {
				blocks = append(blocks, *block)
			}
		case "input_audio":
			// Anthropic does not support audio input — skip and warn rather
			// than sending audio as type:"image" which will always be rejected.
			if p.InputAudio != nil {
				slog.Warn("anthropic: input_audio content type not supported, skipping part",
					"format", p.InputAudio.Format)
			}
			continue
		case "file":
			if p.File == nil || p.File.FileID == "" {
				continue
			}
			// file_id holds the URL; format holds the MIME type (e.g. "video/mp4").
			// Pass through as a URL-based source block.
			blocks = append(blocks, anthropicContentBlock{
				Type: "image", // Anthropic uses "image" type with source block for all binary data
				Source: &imageSource{
					Type: "url",
					URL:  p.File.FileID,
				},
			})
		}
	}
	return blocks
}

// convertImageToAnthropic converts an image URL (data URI or HTTP URL) to Anthropic image block.
func convertImageToAnthropic(imageURL string) *anthropicContentBlock {
	// Handle data URIs: data:image/png;base64,iVBOR...
	if strings.HasPrefix(imageURL, "data:") {
		mediaType, data := parseDataURI(imageURL)
		if data == "" {
			return nil
		}
		return &anthropicContentBlock{
			Type: "image",
			Source: &imageSource{
				Type:      "base64",
				MediaType: mediaType,
				Data:      data,
			},
		}
	}

	// HTTP URLs: pass as URL type.
	return &anthropicContentBlock{
		Type: "image",
		Source: &imageSource{
			Type: "url",
			URL:  imageURL,
		},
	}
}

// parseDataURI extracts media type and base64 data from a data URI.
func parseDataURI(uri string) (mediaType, data string) {
	// data:image/png;base64,iVBOR...
	after := strings.TrimPrefix(uri, "data:")
	semicolonIdx := strings.Index(after, ";")
	if semicolonIdx < 0 {
		return "", ""
	}
	mediaType = after[:semicolonIdx]

	rest := after[semicolonIdx+1:]
	if strings.HasPrefix(rest, "base64,") {
		data = strings.TrimPrefix(rest, "base64,")
	}
	return mediaType, data
}

func translateToolChoice(raw json.RawMessage) (*anthropicToolChoice, error) {
	// Try as string first: "auto", "none", "required".
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		switch s {
		case "auto":
			return &anthropicToolChoice{Type: "auto"}, nil
		case "none":
			return nil, nil // Anthropic doesn't have "none" — just omit tools.
		case "required":
			return &anthropicToolChoice{Type: "any"}, nil
		}
		return nil, nil
	}

	// Try as object: {"type":"function","function":{"name":"X"}}.
	var obj struct {
		Type     string `json:"type"`
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if err := json.Unmarshal(raw, &obj); err == nil && obj.Function.Name != "" {
		return &anthropicToolChoice{Type: "tool", Name: obj.Function.Name}, nil
	}

	return nil, nil
}

func translateResponse(resp *anthropicResponse) *models.ChatCompletionResponse {
	msg := models.Message{
		Role: "assistant",
	}

	var textParts []string
	var toolCalls []models.ToolCall
	var hasMultipart bool // true if response contains image blocks

	// Collect thinking blocks for the canonical ThinkingBlocks field.
	type thinkingBlock struct {
		Type      string `json:"type"`
		Thinking  string `json:"thinking,omitempty"`
		Signature string `json:"signature,omitempty"`
	}
	var thinkingBlocks []thinkingBlock

	for _, block := range resp.Content {
		switch block.Type {
		case "text":
			textParts = append(textParts, block.Text)
		case "thinking":
			// Anthropic extended thinking blocks (Claude 3.7+).
			// Stash on the canonical message so the inbound translator can
			// promote them back to Anthropic thinking content blocks on the
			// response path.
			if block.Thinking != "" {
				thinkingBlocks = append(thinkingBlocks, thinkingBlock{
					Type:     "thinking",
					Thinking: block.Thinking,
				})
			}
		case "tool_use":
			args, _ := json.Marshal(block.Input)
			toolCalls = append(toolCalls, models.ToolCall{
				ID:   block.ID,
				Type: "function",
				Function: models.FunctionCall{
					Name:      block.Name,
					Arguments: string(args),
				},
			})
		case "image":
			hasMultipart = true
		}
	}

	if len(toolCalls) > 0 {
		msg.ToolCalls = toolCalls
	}

	// Serialize thinking blocks onto the canonical message field so the inbound
	// translation layer can promote them back to Anthropic thinking blocks.
	if len(thinkingBlocks) > 0 {
		if b, err := json.Marshal(thinkingBlocks); err == nil {
			msg.ThinkingBlocks = b
		}
	}

	// If the response contains image blocks, use OpenAI multipart content format
	// so that both text and images are preserved.
	if hasMultipart {
		var parts []json.RawMessage
		for _, block := range resp.Content {
			switch block.Type {
			case "text":
				part, _ := json.Marshal(map[string]string{"type": "text", "text": block.Text})
				parts = append(parts, part)
			case "thinking":
				if block.Thinking != "" {
					part, _ := json.Marshal(map[string]string{
						"type": "text",
						"text": "<thinking>\n" + block.Thinking + "\n</thinking>",
					})
					parts = append(parts, part)
				}
			case "image":
				if block.Source != nil {
					var dataURL string
					if block.Source.Type == "base64" && block.Source.Data != "" {
						mediaType := block.Source.MediaType
						if mediaType == "" {
							mediaType = "image/png"
						}
						dataURL = "data:" + mediaType + ";base64," + block.Source.Data
					} else if block.Source.URL != "" {
						dataURL = block.Source.URL
					}
					if dataURL != "" {
						part, _ := json.Marshal(map[string]interface{}{
							"type": "image_url",
							"image_url": map[string]string{
								"url": dataURL,
							},
						})
						parts = append(parts, part)
					}
				}
			}
		}
		if len(parts) > 0 {
			content, _ := json.Marshal(parts)
			msg.Content = content
		}
	} else if len(textParts) > 0 {
		// Simple string format for backward compatibility.
		combined := strings.Join(textParts, "")
		msg.Content = json.RawMessage(mustMarshal(combined))
	}

	return &models.ChatCompletionResponse{
		ID:      resp.ID,
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   resp.Model,
		Choices: []models.Choice{
			{
				Index:        0,
				Message:      msg,
				FinishReason: mapStopReason(resp.StopReason),
			},
		},
		Usage: &models.Usage{
			PromptTokens:     resp.Usage.InputTokens,
			CompletionTokens: resp.Usage.OutputTokens,
			TotalTokens:      resp.Usage.InputTokens + resp.Usage.OutputTokens,
		},
	}
}

func mapStopReason(reason string) string {
	switch reason {
	case "end_turn":
		return "stop"
	case "max_tokens":
		return "length"
	case "stop_sequence":
		return "stop"
	case "tool_use":
		return "tool_calls"
	default:
		return "stop"
	}
}

func parseAnthropicError(status int, body []byte) *models.APIError {
	var errResp anthropicErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		return &models.APIError{
			Status:  mapAnthropicStatus(status, errResp.Error.Type),
			Type:    mapAnthropicErrorType(errResp.Error.Type),
			Code:    "provider_" + errResp.Error.Type,
			Message: errResp.Error.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("anthropic error (HTTP %d): %s", status, msg))
}

func mapAnthropicStatus(httpStatus int, errorType string) int {
	switch errorType {
	case "invalid_request_error":
		return http.StatusBadRequest
	case "authentication_error":
		return http.StatusUnauthorized
	case "permission_error":
		return http.StatusForbidden
	case "not_found_error":
		return http.StatusNotFound
	case "rate_limit_error":
		return http.StatusTooManyRequests
	case "overloaded_error":
		return http.StatusBadGateway
	default:
		if httpStatus >= 400 {
			return http.StatusBadGateway
		}
		return http.StatusBadGateway
	}
}

func mapAnthropicErrorType(errorType string) string {
	switch errorType {
	case "invalid_request_error":
		return models.ErrTypeInvalidRequest
	case "authentication_error":
		return models.ErrTypeAuthentication
	case "permission_error":
		return models.ErrTypePermission
	case "not_found_error":
		return models.ErrTypeNotFound
	case "rate_limit_error":
		return models.ErrTypeRateLimit
	default:
		return models.ErrTypeUpstream
	}
}

// --- Helpers ---

func extractTextContent(content json.RawMessage) string {
	if len(content) == 0 {
		return ""
	}
	// Try as string.
	var s string
	if err := json.Unmarshal(content, &s); err == nil {
		return s
	}
	// Try as array of content parts — concatenate all text blocks.
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(content, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" {
				texts = append(texts, p.Text)
			}
		}
		return strings.Join(texts, "")
	}
	return ""
}

func mustMarshal(v interface{}) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func resolveModelName(model string) string {
	for i := 0; i < len(model); i++ {
		if model[i] == '/' {
			return model[i+1:]
		}
	}
	return model
}
