package bedrock

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// sharedDownloadClient is a package-level HTTP client with connection pooling
// for downloading images and files from external URLs.
var sharedDownloadClient = &http.Client{
	Timeout: 30 * time.Second,
	Transport: &http.Transport{
		MaxIdleConns:        20,
		MaxIdleConnsPerHost: 5,
		IdleConnTimeout:     90 * time.Second,
		// Use a custom DialContext to enforce SSRF protection at connect time.
		DialContext: ssrfSafeDialContext,
	},
}

// ssrfSafeDialContext wraps the default dialer and rejects connections to
// private, loopback, link-local, and reserved IP addresses. This prevents
// SSRF attacks where an attacker passes URLs like http://169.254.169.254/.
func ssrfSafeDialContext(ctx context.Context, network, addr string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return nil, fmt.Errorf("invalid address %q: %w", addr, err)
	}

	ips, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, fmt.Errorf("DNS resolution failed for %q: %w", host, err)
	}

	for _, ipAddr := range ips {
		if ipAddr.IP.IsPrivate() || ipAddr.IP.IsLoopback() || ipAddr.IP.IsLinkLocalUnicast() ||
			ipAddr.IP.IsLinkLocalMulticast() || ipAddr.IP.IsUnspecified() {
			return nil, fmt.Errorf("URL resolves to private/reserved IP %s — request blocked (SSRF protection)", ipAddr.IP)
		}
	}

	// All IPs passed validation; connect to the original address.
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	return dialer.DialContext(ctx, network, net.JoinHostPort(host, port))
}

// Bedrock uses Anthropic Messages format for Claude models.
// These types mirror the Anthropic format used by Bedrock's Messages API.

type bedrockRequest struct {
	AnthropicVersion string               `json:"anthropic_version"`
	Model            string               `json:"-"` // Used in URL path, not body.
	Messages         []bedrockMessage     `json:"messages"`
	System           string               `json:"system,omitempty"`
	MaxTokens        int                  `json:"max_tokens"`
	Temperature      *float64             `json:"temperature,omitempty"`
	TopP             *float64             `json:"top_p,omitempty"`
	StopSequences    []string             `json:"stop_sequences,omitempty"`
	Stream           bool                 `json:"-"` // Determined by endpoint, not body field.
	Tools            []bedrockTool        `json:"tools,omitempty"`
	ToolChoice       *bedrockToolChoice   `json:"tool_choice,omitempty"`
	OutputConfig     *bedrockOutputConfig `json:"output_config,omitempty"`
}

type bedrockOutputConfig struct {
	Format *bedrockOutputFormat `json:"format,omitempty"`
}

type bedrockOutputFormat struct {
	Type   string          `json:"type"`
	Schema json.RawMessage `json:"schema,omitempty"`
}

type bedrockMessage struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

type bedrockContentBlock struct {
	Type      string          `json:"type"`
	Text      string          `json:"text,omitempty"`
	ID        string          `json:"id,omitempty"`
	Name      string          `json:"name,omitempty"`
	Input     json.RawMessage `json:"input,omitempty"`
	ToolUseID string          `json:"tool_use_id,omitempty"`
	Content   json.RawMessage `json:"content,omitempty"`
	Source    *imageSource    `json:"source,omitempty"`
}

type imageSource struct {
	Type      string `json:"type"`
	MediaType string `json:"media_type,omitempty"`
	Data      string `json:"data,omitempty"`
	URL       string `json:"url,omitempty"`
}

type bedrockTool struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	InputSchema json.RawMessage `json:"input_schema"`
}

type bedrockToolChoice struct {
	Type string `json:"type"`
	Name string `json:"name,omitempty"`
}

type bedrockResponse struct {
	ID         string                `json:"id"`
	Type       string                `json:"type"`
	Model      string                `json:"model"`
	Role       string                `json:"role"`
	Content    []bedrockContentBlock `json:"content"`
	StopReason string                `json:"stop_reason"`
	Usage      bedrockUsage          `json:"usage"`
}

type bedrockUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// --- Translation functions ---

func translateRequest(req *models.ChatCompletionRequest) (*bedrockRequest, string) {
	modelID := resolveModelName(req.Model)

	br := &bedrockRequest{
		AnthropicVersion: "bedrock-2023-05-31",
		Model:            modelID,
		Temperature:      req.Temperature,
		TopP:             req.TopP,
	}

	// Set max_tokens.
	switch {
	case req.MaxTokens != nil:
		br.MaxTokens = *req.MaxTokens
	case req.MaxCompletionTokens != nil:
		br.MaxTokens = *req.MaxCompletionTokens
	default:
		br.MaxTokens = 4096
	}

	// Parse stop sequences.
	if len(req.Stop) > 0 {
		var stops []string
		if err := json.Unmarshal(req.Stop, &stops); err != nil {
			var single string
			if err := json.Unmarshal(req.Stop, &single); err == nil {
				stops = []string{single}
			}
		}
		br.StopSequences = stops
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

		bm := translateMessage(msg)
		// Bedrock requires alternating user/assistant roles.
		// Multiple OpenAI "tool" messages become "user" role with
		// tool_result content — merge consecutive same-role messages.
		if len(br.Messages) > 0 && bm.Role == br.Messages[len(br.Messages)-1].Role {
			// Merge content arrays.
			var existing []bedrockContentBlock
			json.Unmarshal(br.Messages[len(br.Messages)-1].Content, &existing)
			var additional []bedrockContentBlock
			json.Unmarshal(bm.Content, &additional)
			existing = append(existing, additional...)
			merged, _ := json.Marshal(existing)
			br.Messages[len(br.Messages)-1].Content = merged
		} else {
			br.Messages = append(br.Messages, bm)
		}
	}

	if len(systemParts) > 0 {
		system := systemParts[0]
		for i := 1; i < len(systemParts); i++ {
			system += "\n\n" + systemParts[i]
		}
		br.System = system
	}

	// Translate tools.
	if len(req.Tools) > 0 {
		for _, t := range req.Tools {
			if t.Type != "function" {
				continue
			}
			br.Tools = append(br.Tools, bedrockTool{
				Name:        t.Function.Name,
				Description: t.Function.Description,
				InputSchema: t.Function.Parameters,
			})
		}
	}

	// Translate tool_choice.
	if len(req.ToolChoice) > 0 {
		tc := translateToolChoice(req.ToolChoice)
		if tc != nil {
			br.ToolChoice = tc
		}
	}

	if schema := extractResponseFormatSchema(req.ResponseFormat); len(schema) > 0 {
		br.OutputConfig = &bedrockOutputConfig{
			Format: &bedrockOutputFormat{
				Type:   "json_schema",
				Schema: schema,
			},
		}
	}

	return br, modelID
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

func translateMessage(msg models.Message) bedrockMessage {
	bm := bedrockMessage{
		Role: msg.Role,
	}

	// Handle tool result messages.
	if msg.Role == "tool" {
		block := bedrockContentBlock{
			Type:      "tool_result",
			ToolUseID: msg.ToolCallID,
		}
		text := extractTextContent(msg.Content)
		if text != "" {
			block.Content = json.RawMessage(fmt.Sprintf(`[{"type":"text","text":%s}]`, mustMarshal(text)))
		}
		content, _ := json.Marshal([]bedrockContentBlock{block})
		bm.Content = content
		return bm
	}

	// Handle assistant messages with tool calls.
	if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
		var blocks []bedrockContentBlock
		text := extractTextContent(msg.Content)
		if text != "" {
			blocks = append(blocks, bedrockContentBlock{Type: "text", Text: text})
		}
		for _, tc := range msg.ToolCalls {
			blocks = append(blocks, bedrockContentBlock{
				Type:  "tool_use",
				ID:    tc.ID,
				Name:  tc.Function.Name,
				Input: json.RawMessage(tc.Function.Arguments),
			})
		}
		content, _ := json.Marshal(blocks)
		bm.Content = content
		return bm
	}

	// Standard message: check vision content first (handles both text + images),
	// then fall back to text-only, then pass-through.
	if blocks := translateVisionContent(msg.Content); blocks != nil {
		content, _ := json.Marshal(blocks)
		bm.Content = content
	} else if text := extractTextContent(msg.Content); text != "" {
		blocks := []bedrockContentBlock{{Type: "text", Text: text}}
		content, _ := json.Marshal(blocks)
		bm.Content = content
	} else {
		bm.Content = msg.Content
	}

	return bm
}

// translateVisionContent converts OpenAI vision content parts to Bedrock (Anthropic) format.
// Handles image_url, input_audio, and file content part types.
func translateVisionContent(content json.RawMessage) []bedrockContentBlock {
	if len(content) == 0 {
		return nil
	}

	var parts []struct {
		Type     string `json:"type"`
		Text     string `json:"text"`
		ImageURL *struct {
			URL string `json:"url"`
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

	var blocks []bedrockContentBlock
	for _, p := range parts {
		switch p.Type {
		case "text":
			blocks = append(blocks, bedrockContentBlock{Type: "text", Text: p.Text})
		case "image_url":
			if p.ImageURL == nil {
				continue
			}
			block := convertImageToBedrock(p.ImageURL.URL)
			if block != nil {
				blocks = append(blocks, *block)
			}
		case "input_audio":
			// Bedrock Claude does not support audio input — skip and warn rather
			// than sending audio as type:"image" which will always fail.
			if p.InputAudio != nil {
				slog.Warn("bedrock: input_audio content type not supported, skipping part",
					"format", p.InputAudio.Format)
			}
			continue
		case "file":
			if p.File == nil || p.File.FileID == "" {
				continue
			}
			// file_id holds the URL; format holds the MIME type (e.g. "video/mp4").
			// Bedrock only supports base64-encoded data, so download and encode.
			mediaType, data, err := downloadImageAsBase64(p.File.FileID)
			if err != nil {
				continue
			}
			// Prefer the format from the content part if provided.
			if p.File.Format != "" {
				mediaType = p.File.Format
			}
			blocks = append(blocks, bedrockContentBlock{
				Type: "image", // Bedrock uses "image" type with source block for all binary data
				Source: &imageSource{
					Type:      "base64",
					MediaType: mediaType,
					Data:      data,
				},
			})
		}
	}
	return blocks
}

func convertImageToBedrock(imageURL string) *bedrockContentBlock {
	if strings.HasPrefix(imageURL, "data:") {
		mediaType, data := parseDataURI(imageURL)
		if data == "" {
			return nil
		}
		return &bedrockContentBlock{
			Type: "image",
			Source: &imageSource{
				Type:      "base64",
				MediaType: mediaType,
				Data:      data,
			},
		}
	}

	// Bedrock Claude only supports base64-encoded images, not URL references.
	// Download the image and convert to base64.
	if strings.HasPrefix(imageURL, "http://") || strings.HasPrefix(imageURL, "https://") {
		mediaType, data, err := downloadImageAsBase64(imageURL)
		if err != nil {
			return nil
		}
		return &bedrockContentBlock{
			Type: "image",
			Source: &imageSource{
				Type:      "base64",
				MediaType: mediaType,
				Data:      data,
			},
		}
	}

	return nil
}

// downloadImageAsBase64 fetches an image from a URL and returns it as base64-encoded data.
// The function uses a shared HTTP client with SSRF protection that rejects private/reserved IPs.
func downloadImageAsBase64(rawURL string) (mediaType, data string, err error) {
	resp, err := sharedDownloadClient.Get(rawURL)
	if err != nil {
		return "", "", fmt.Errorf("downloading image: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("downloading image: HTTP %d", resp.StatusCode)
	}

	// Limit to 20MB to prevent abuse.
	body, err := io.ReadAll(io.LimitReader(resp.Body, 20*1024*1024))
	if err != nil {
		return "", "", fmt.Errorf("reading image body: %w", err)
	}

	// Determine media type from Content-Type header, falling back to detection from URL.
	contentType := resp.Header.Get("Content-Type")
	if contentType != "" {
		// Strip parameters like "; charset=utf-8".
		if idx := strings.Index(contentType, ";"); idx >= 0 {
			contentType = strings.TrimSpace(contentType[:idx])
		}
		mediaType = contentType
	} else {
		mediaType = inferMediaType(rawURL)
	}

	data = base64.StdEncoding.EncodeToString(body)
	return mediaType, data, nil
}

// inferMediaType guesses the media type from a URL's file extension.
func inferMediaType(url string) string {
	lower := strings.ToLower(url)
	switch {
	case strings.HasSuffix(lower, ".png"):
		return "image/png"
	case strings.HasSuffix(lower, ".gif"):
		return "image/gif"
	case strings.HasSuffix(lower, ".webp"):
		return "image/webp"
	default:
		return "image/jpeg"
	}
}

func parseDataURI(uri string) (mediaType, data string) {
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

func translateToolChoice(raw json.RawMessage) *bedrockToolChoice {
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		switch s {
		case "auto":
			return &bedrockToolChoice{Type: "auto"}
		case "required":
			return &bedrockToolChoice{Type: "any"}
		default:
			return nil
		}
	}

	var obj struct {
		Type     string `json:"type"`
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if err := json.Unmarshal(raw, &obj); err == nil && obj.Function.Name != "" {
		return &bedrockToolChoice{Type: "tool", Name: obj.Function.Name}
	}

	return nil
}

func translateResponse(resp *bedrockResponse) *models.ChatCompletionResponse {
	msg := models.Message{
		Role: "assistant",
	}

	var textParts []string
	var toolCalls []models.ToolCall
	var hasMultipart bool // true if response contains image blocks

	for _, block := range resp.Content {
		switch block.Type {
		case "text":
			textParts = append(textParts, block.Text)
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

	// If the response contains image blocks, use OpenAI multipart content format
	// so that both text and images are preserved.
	if hasMultipart {
		var parts []json.RawMessage
		for _, block := range resp.Content {
			switch block.Type {
			case "text":
				part, _ := json.Marshal(map[string]string{"type": "text", "text": block.Text})
				parts = append(parts, part)
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
		combined := textParts[0]
		for i := 1; i < len(textParts); i++ {
			combined += textParts[i]
		}
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

func parseBedrockError(status int, body []byte) *models.APIError {
	// Try Bedrock error format.
	var errResp struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	}
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Message != "" {
		return &models.APIError{
			Status:  mapBedrockStatus(status),
			Type:    mapBedrockErrorType(status),
			Code:    fmt.Sprintf("provider_%d", status),
			Message: errResp.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("bedrock error (HTTP %d): %s", status, msg))
}

func mapBedrockStatus(status int) int {
	switch {
	case status == 429:
		return http.StatusTooManyRequests
	case status >= 500:
		return http.StatusBadGateway
	case status >= 400:
		return status
	default:
		return http.StatusBadGateway
	}
}

func mapBedrockErrorType(status int) string {
	switch {
	case status == 401 || status == 403:
		return models.ErrTypeAuthentication
	case status == 404:
		return models.ErrTypeNotFound
	case status == 429:
		return models.ErrTypeRateLimit
	case status >= 400 && status < 500:
		return models.ErrTypeInvalidRequest
	default:
		return models.ErrTypeUpstream
	}
}

// --- Helpers ---

func extractTextContent(content json.RawMessage) string {
	if len(content) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(content, &s); err == nil {
		return s
	}
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
