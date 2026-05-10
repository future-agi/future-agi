package gemini

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// --- Gemini native types ---

type geminiRequest struct {
	Contents          []geminiContent          `json:"contents"`
	SystemInstruction *geminiContent           `json:"systemInstruction,omitempty"`
	GenerationConfig  *geminiGenerationConfig  `json:"generationConfig,omitempty"`
	Tools             []geminiToolDeclarations `json:"tools,omitempty"`
	ToolConfig        *geminiToolConfig        `json:"toolConfig,omitempty"`
}

type geminiToolConfig struct {
	FunctionCallingConfig *geminiFunctionCallingConfig `json:"functionCallingConfig,omitempty"`
}

type geminiFunctionCallingConfig struct {
	Mode                 string   `json:"mode"`
	AllowedFunctionNames []string `json:"allowedFunctionNames,omitempty"`
}

type geminiContent struct {
	Role  string       `json:"role,omitempty"`
	Parts []geminiPart `json:"parts"`
}

type geminiPart struct {
	Text             string              `json:"text,omitempty"`
	InlineData       *geminiInlineData   `json:"inlineData,omitempty"`
	FileData         *geminiFileData     `json:"fileData,omitempty"`
	FunctionCall     *geminiFunctionCall `json:"functionCall,omitempty"`
	FunctionResponse *geminiFuncResponse `json:"functionResponse,omitempty"`
}

type geminiFileData struct {
	MimeType string `json:"mimeType"`
	FileURI  string `json:"fileUri"`
}

type geminiInlineData struct {
	MimeType string `json:"mimeType"`
	Data     string `json:"data"`
}

type geminiFunctionCall struct {
	Name string          `json:"name"`
	Args json.RawMessage `json:"args"`
}

type geminiFuncResponse struct {
	Name     string          `json:"name"`
	Response json.RawMessage `json:"response"`
}

type geminiGenerationConfig struct {
	Temperature        *float64            `json:"temperature,omitempty"`
	TopP               *float64            `json:"topP,omitempty"`
	MaxOutputTokens    *int                `json:"maxOutputTokens,omitempty"`
	StopSequences      []string            `json:"stopSequences,omitempty"`
	ResponseMimeType   string              `json:"responseMimeType,omitempty"`
	ResponseSchema     json.RawMessage     `json:"responseSchema,omitempty"`
	ResponseModalities []string            `json:"responseModalities,omitempty"`
	SpeechConfig       *geminiSpeechConfig `json:"speechConfig,omitempty"`
}

type geminiSpeechConfig struct {
	VoiceConfig *geminiVoiceConfig `json:"voiceConfig,omitempty"`
}

type geminiVoiceConfig struct {
	PrebuiltVoiceConfig *geminiPrebuiltVoiceConfig `json:"prebuiltVoiceConfig,omitempty"`
}

type geminiPrebuiltVoiceConfig struct {
	VoiceName string `json:"voiceName"`
}

type geminiToolDeclarations struct {
	FunctionDeclarations []geminiFuncDecl `json:"functionDeclarations,omitempty"`
}

type geminiFuncDecl struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
}

type geminiResponse struct {
	Candidates    []geminiCandidate    `json:"candidates"`
	UsageMetadata *geminiUsageMetadata `json:"usageMetadata,omitempty"`
	ModelVersion  string               `json:"modelVersion,omitempty"`
}

type geminiCandidate struct {
	Content      geminiContent `json:"content"`
	FinishReason string        `json:"finishReason,omitempty"`
}

type geminiUsageMetadata struct {
	PromptTokenCount     int `json:"promptTokenCount"`
	CandidatesTokenCount int `json:"candidatesTokenCount"`
	TotalTokenCount      int `json:"totalTokenCount"`
}

type geminiErrorResponse struct {
	Error geminiErrorDetail `json:"error"`
}

type geminiErrorDetail struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Status  string `json:"status"`
}

// --- Translation functions ---

func translateRequest(req *models.ChatCompletionRequest) (*geminiRequest, string) {
	gr := &geminiRequest{}

	model := resolveModelName(req.Model)

	// Extract system messages.
	var systemParts []geminiPart
	for _, msg := range req.Messages {
		if msg.Role == "system" {
			text := extractTextContent(msg.Content)
			if text != "" {
				systemParts = append(systemParts, geminiPart{Text: text})
			}
			continue
		}

		gc := translateMessage(msg)
		gr.Contents = append(gr.Contents, gc)
	}

	if len(systemParts) > 0 {
		gr.SystemInstruction = &geminiContent{Parts: systemParts}
	}

	// Build generation config.
	gc := &geminiGenerationConfig{
		Temperature: req.Temperature,
		TopP:        req.TopP,
	}

	if req.MaxTokens != nil {
		gc.MaxOutputTokens = req.MaxTokens
	} else if req.MaxCompletionTokens != nil {
		gc.MaxOutputTokens = req.MaxCompletionTokens
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
		gc.StopSequences = stops
	}

	// Handle response format (JSON mode / structured output).
	if req.ResponseFormat != nil {
		switch req.ResponseFormat.Type {
		case "json_object":
			gc.ResponseMimeType = "application/json"
		case "json_schema":
			gc.ResponseMimeType = "application/json"
			if len(req.ResponseFormat.JSONSchema) > 0 {
				// Extract the schema object from the json_schema wrapper.
				var schemaWrapper struct {
					Schema json.RawMessage `json:"schema"`
				}
				if json.Unmarshal(req.ResponseFormat.JSONSchema, &schemaWrapper) == nil && len(schemaWrapper.Schema) > 0 {
					gc.ResponseSchema = schemaWrapper.Schema
				} else {
					gc.ResponseSchema = req.ResponseFormat.JSONSchema
				}
			}
		}
	}

	// Handle audio modalities: if the request has modalities containing "audio",
	// set responseModalities to ["AUDIO"] and configure speechConfig from the audio config.
	if hasAudioModality(req.Modalities) {
		gc.ResponseModalities = []string{"AUDIO"}
		if req.Audio != nil && req.Audio.Voice != "" {
			gc.SpeechConfig = &geminiSpeechConfig{
				VoiceConfig: &geminiVoiceConfig{
					PrebuiltVoiceConfig: &geminiPrebuiltVoiceConfig{
						VoiceName: mapVoiceToGemini(req.Audio.Voice),
					},
				},
			}
		}
	}

	gr.GenerationConfig = gc

	// Translate tools.
	if len(req.Tools) > 0 {
		var decls []geminiFuncDecl
		for _, t := range req.Tools {
			if t.Type != "function" {
				continue
			}
			decls = append(decls, geminiFuncDecl{
				Name:        t.Function.Name,
				Description: t.Function.Description,
				Parameters:  t.Function.Parameters,
			})
		}
		if len(decls) > 0 {
			gr.Tools = []geminiToolDeclarations{{FunctionDeclarations: decls}}
		}
	}

	// Translate tool_choice → toolConfig.functionCallingConfig.
	if len(req.ToolChoice) > 0 {
		if tc := translateToolChoice(req.ToolChoice); tc != nil {
			gr.ToolConfig = &geminiToolConfig{FunctionCallingConfig: tc}
		}
	}

	return gr, model
}

func translateMessage(msg models.Message) geminiContent {
	gc := geminiContent{
		Role: mapRoleToGemini(msg.Role),
	}

	// Handle tool result messages.
	if msg.Role == "tool" {
		text := extractTextContent(msg.Content)
		gc.Role = "user" // Gemini uses "user" role for function responses.

		// Gemini requires function Name in FunctionResponse.
		// OpenAI tool messages may have name in msg.Name, or only tool_call_id.
		funcName := msg.Name
		if funcName == "" {
			funcName = msg.ToolCallID // fallback: use the tool call ID as identifier
		}
		if funcName == "" {
			funcName = "unknown_function"
		}

		gc.Parts = []geminiPart{
			{
				FunctionResponse: &geminiFuncResponse{
					Name:     funcName,
					Response: json.RawMessage(fmt.Sprintf(`{"result":%s}`, mustMarshal(text))),
				},
			},
		}
		return gc
	}

	// Handle assistant messages with tool calls.
	if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
		for _, tc := range msg.ToolCalls {
			gc.Parts = append(gc.Parts, geminiPart{
				FunctionCall: &geminiFunctionCall{
					Name: tc.Function.Name,
					Args: json.RawMessage(tc.Function.Arguments),
				},
			})
		}
		// Also include text content if present.
		text := extractTextContent(msg.Content)
		if text != "" {
			gc.Parts = append([]geminiPart{{Text: text}}, gc.Parts...)
		}
		return gc
	}

	// Standard message: check vision content first (handles both text + images),
	// then fall back to text-only.
	if parts := translateVisionContent(msg.Content); parts != nil {
		gc.Parts = parts
	} else if text := extractTextContent(msg.Content); text != "" {
		gc.Parts = []geminiPart{{Text: text}}
	} else {
		gc.Parts = []geminiPart{{Text: ""}}
	}

	return gc
}

// translateVisionContent converts OpenAI vision content parts to Gemini parts.
// OpenAI: [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]
// OpenAI: [{"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}]
// OpenAI: [{"type":"file","file":{"file_id":"https://example.com/video.mp4","format":"video/mp4"}}]
// Gemini: [{text:"..."}, {inlineData:{mimeType:"image/png",data:"..."}}]
func translateVisionContent(content json.RawMessage) []geminiPart {
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
			FileID   string `json:"file_id"`
			FileData string `json:"file_data"`
			Filename string `json:"filename"`
			Format   string `json:"format"`
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

	var geminiParts []geminiPart
	for _, p := range parts {
		switch p.Type {
		case "text":
			if p.Text != "" {
				geminiParts = append(geminiParts, geminiPart{Text: p.Text})
			}
		case "image_url":
			if p.ImageURL == nil {
				continue
			}
			if part := parseImageURLToPart(p.ImageURL.URL); part != nil {
				geminiParts = append(geminiParts, *part)
			}
		case "input_audio":
			if p.InputAudio == nil || p.InputAudio.Data == "" {
				continue
			}
			mimeType := common.MapAudioFormatToMime(p.InputAudio.Format)
			geminiParts = append(geminiParts, geminiPart{
				InlineData: &geminiInlineData{
					MimeType: mimeType,
					Data:     p.InputAudio.Data,
				},
			})
		case "file":
			if p.File == nil {
				continue
			}
			// ``file_data`` is a data URI (``data:<mime>;base64,<b64>``) —
			// used for uploaded PDFs and documents that the caller has
			// already base64-encoded. ``file_id`` carries a fetchable URL
			// (Gemini will fetch it via fileUri). Either is acceptable.
			if p.File.FileData != "" {
				if part := parseImageURLToPart(p.File.FileData); part != nil {
					geminiParts = append(geminiParts, *part)
				}
				continue
			}
			if p.File.FileID == "" {
				continue
			}
			// ``format`` carries the MIME type (e.g. "video/mp4"); fall
			// back to a permissive default so Gemini at least sees the file.
			mimeType := p.File.Format
			if mimeType == "" {
				mimeType = "application/octet-stream"
			}
			geminiParts = append(geminiParts, geminiPart{
				FileData: &geminiFileData{
					MimeType: mimeType,
					FileURI:  p.File.FileID,
				},
			})
		}
	}
	return geminiParts
}

// parseImageURLToPart converts an image URL to the appropriate Gemini part.
// Data URIs (data:image/png;base64,...) → inlineData.
// HTTP/HTTPS URLs → fileData (Gemini supports fileUri for remote images).
func parseImageURLToPart(imageURL string) *geminiPart {
	if strings.HasPrefix(imageURL, "data:") {
		mediaType, data := parseDataURI(imageURL)
		if data == "" {
			return nil
		}
		return &geminiPart{
			InlineData: &geminiInlineData{
				MimeType: mediaType,
				Data:     data,
			},
		}
	}
	if strings.HasPrefix(imageURL, "http://") || strings.HasPrefix(imageURL, "https://") {
		// Infer MIME type from URL extension; default to a generic type.
		mimeType := inferImageMimeType(imageURL)
		return &geminiPart{
			FileData: &geminiFileData{
				MimeType: mimeType,
				FileURI:  imageURL,
			},
		}
	}
	return nil
}

// inferImageMimeType guesses a MIME type from the URL path extension.
func inferImageMimeType(url string) string {
	lower := strings.ToLower(url)
	// Strip query/fragment before checking extension.
	if idx := strings.IndexAny(lower, "?#"); idx != -1 {
		lower = lower[:idx]
	}
	switch {
	case strings.HasSuffix(lower, ".png"):
		return "image/png"
	case strings.HasSuffix(lower, ".jpg"), strings.HasSuffix(lower, ".jpeg"):
		return "image/jpeg"
	case strings.HasSuffix(lower, ".gif"):
		return "image/gif"
	case strings.HasSuffix(lower, ".webp"):
		return "image/webp"
	case strings.HasSuffix(lower, ".svg"):
		return "image/svg+xml"
	default:
		return "image/jpeg" // safe default for most image URLs
	}
}

// parseDataURI extracts media type and base64 data from a data URI.
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

func translateResponse(resp *geminiResponse, model string) *models.ChatCompletionResponse {
	result := &models.ChatCompletionResponse{
		ID:      fmt.Sprintf("gen-%d", time.Now().UnixNano()),
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   model,
	}

	if len(resp.Candidates) > 0 {
		candidate := resp.Candidates[0]
		msg := models.Message{Role: "assistant"}

		var textParts []string
		var imageParts []geminiInlineData
		var audioParts []geminiInlineData
		var toolCalls []models.ToolCall
		toolCallIdx := 0

		for _, part := range candidate.Content.Parts {
			if part.Text != "" {
				textParts = append(textParts, part.Text)
			}
			if part.InlineData != nil {
				if isAudioMimeType(part.InlineData.MimeType) {
					audioParts = append(audioParts, *part.InlineData)
				} else {
					imageParts = append(imageParts, *part.InlineData)
				}
			}
			if part.FunctionCall != nil {
				toolCalls = append(toolCalls, models.ToolCall{
					ID:   fmt.Sprintf("call_%d", toolCallIdx),
					Type: "function",
					Function: models.FunctionCall{
						Name:      part.FunctionCall.Name,
						Arguments: string(part.FunctionCall.Args),
					},
				})
				toolCallIdx++
			}
		}

		if len(audioParts) > 0 {
			// Response contains audio — use OpenAI multipart content format with audio parts:
			// [{"type":"text","text":"..."},{"type":"audio","audio":{"data":"<base64>","format":"pcm16"}}]
			var contentParts []map[string]interface{}
			if len(textParts) > 0 {
				combined := strings.Join(textParts, "")
				contentParts = append(contentParts, map[string]interface{}{
					"type": "text",
					"text": combined,
				})
			}
			for _, aud := range audioParts {
				contentParts = append(contentParts, map[string]interface{}{
					"type": "audio",
					"audio": map[string]string{
						"data":   aud.Data,
						"format": audioMimeToFormat(aud.MimeType),
					},
				})
			}
			msg.Content = json.RawMessage(mustMarshal(contentParts))
		} else if len(imageParts) > 0 {
			// Response contains images — use OpenAI multipart content format:
			// [{"type":"text","text":"..."},{"type":"image_url","image_url":{"url":"data:mime;base64,..."}}]
			var contentParts []map[string]interface{}
			if len(textParts) > 0 {
				combined := strings.Join(textParts, "")
				contentParts = append(contentParts, map[string]interface{}{
					"type": "text",
					"text": combined,
				})
			}
			for _, img := range imageParts {
				dataURI := fmt.Sprintf("data:%s;base64,%s", img.MimeType, img.Data)
				contentParts = append(contentParts, map[string]interface{}{
					"type": "image_url",
					"image_url": map[string]string{
						"url": dataURI,
					},
				})
			}
			msg.Content = json.RawMessage(mustMarshal(contentParts))
		} else if len(textParts) > 0 {
			// Text-only response — keep the simple string format for backward compat.
			combined := strings.Join(textParts, "")
			msg.Content = json.RawMessage(mustMarshal(combined))
		}
		if len(toolCalls) > 0 {
			msg.ToolCalls = toolCalls
		}

		result.Choices = []models.Choice{
			{
				Index:        0,
				Message:      msg,
				FinishReason: mapGeminiFinishReasonWithToolCalls(candidate.FinishReason, len(toolCalls) > 0),
			},
		}
	}

	if resp.UsageMetadata != nil {
		result.Usage = &models.Usage{
			PromptTokens:     resp.UsageMetadata.PromptTokenCount,
			CompletionTokens: resp.UsageMetadata.CandidatesTokenCount,
			TotalTokens:      resp.UsageMetadata.TotalTokenCount,
		}
	}

	return result
}

func mapRoleToGemini(role string) string {
	switch role {
	case "assistant":
		return "model"
	case "user":
		return "user"
	default:
		return role
	}
}

func mapGeminiFinishReason(reason string) string {
	switch reason {
	case "STOP":
		return "stop"
	case "MAX_TOKENS":
		return "length"
	case "SAFETY":
		return "content_filter"
	case "RECITATION":
		return "content_filter"
	case "OTHER":
		return "stop"
	default:
		return "stop"
	}
}

func mapGeminiFinishReasonWithToolCalls(reason string, hasToolCalls bool) string {
	if hasToolCalls {
		return "tool_calls"
	}
	return mapGeminiFinishReason(reason)
}

func parseGeminiError(status int, body []byte) *models.APIError {
	var errResp geminiErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		return &models.APIError{
			Status:  mapGeminiStatus(errResp.Error.Status),
			Type:    mapGeminiErrorType(errResp.Error.Status),
			Code:    "provider_" + errResp.Error.Status,
			Message: errResp.Error.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("gemini error (HTTP %d): %s", status, msg))
}

func mapGeminiStatus(status string) int {
	switch status {
	case "INVALID_ARGUMENT":
		return http.StatusBadRequest
	case "UNAUTHENTICATED":
		return http.StatusUnauthorized
	case "PERMISSION_DENIED":
		return http.StatusForbidden
	case "NOT_FOUND":
		return http.StatusNotFound
	case "RESOURCE_EXHAUSTED":
		return http.StatusTooManyRequests
	default:
		return http.StatusBadGateway
	}
}

func mapGeminiErrorType(status string) string {
	switch status {
	case "INVALID_ARGUMENT":
		return models.ErrTypeInvalidRequest
	case "UNAUTHENTICATED":
		return models.ErrTypeAuthentication
	case "PERMISSION_DENIED":
		return models.ErrTypePermission
	case "NOT_FOUND":
		return models.ErrTypeNotFound
	case "RESOURCE_EXHAUSTED":
		return models.ErrTypeRateLimit
	default:
		return models.ErrTypeUpstream
	}
}

func buildURL(baseURL, model string, stream bool) string {
	if isVertexAI(baseURL) {
		return buildVertexURL(baseURL, model, stream)
	}
	if stream {
		return fmt.Sprintf("%s/v1beta/models/%s:streamGenerateContent?alt=sse", baseURL, model)
	}
	return fmt.Sprintf("%s/v1beta/models/%s:generateContent", baseURL, model)
}

// isVertexAI detects Vertex AI base URLs.
func isVertexAI(baseURL string) bool {
	return strings.Contains(baseURL, "aiplatform.googleapis.com")
}

// buildVertexURL constructs the Vertex AI URL format.
// Expects baseURL like: https://us-central1-aiplatform.googleapis.com
// with project/location embedded in the URL path already, or via headers.
func buildVertexURL(baseURL, model string, stream bool) string {
	action := "generateContent"
	if stream {
		action = "streamGenerateContent?alt=sse"
	}
	// If the baseURL already contains a full path with project/location, use it directly.
	// Otherwise, use the v1beta1 models endpoint.
	if strings.Contains(baseURL, "/projects/") {
		return fmt.Sprintf("%s/publishers/google/models/%s:%s", baseURL, model, action)
	}
	return fmt.Sprintf("%s/v1beta1/models/%s:%s", baseURL, model, action)
}

// translateToolChoice maps OpenAI tool_choice to Gemini functionCallingConfig.
// OpenAI: "none" / "auto" / "required" / {"type":"function","function":{"name":"X"}}
// Gemini: mode "NONE" / "AUTO" / "ANY", with optional allowedFunctionNames.
func translateToolChoice(raw json.RawMessage) *geminiFunctionCallingConfig {
	// Try as string first: "auto", "none", "required".
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		switch s {
		case "none":
			return &geminiFunctionCallingConfig{Mode: "NONE"}
		case "auto":
			return &geminiFunctionCallingConfig{Mode: "AUTO"}
		case "required":
			return &geminiFunctionCallingConfig{Mode: "ANY"}
		}
		return nil
	}

	// Try as object: {"type":"function","function":{"name":"X"}}.
	var obj struct {
		Type     string `json:"type"`
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if err := json.Unmarshal(raw, &obj); err == nil && obj.Function.Name != "" {
		// ANY mode with allowedFunctionNames restricts to the named function.
		return &geminiFunctionCallingConfig{
			Mode:                 "ANY",
			AllowedFunctionNames: []string{obj.Function.Name},
		}
	}

	return nil
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

// hasAudioModality checks if "audio" is present in the modalities list.
func hasAudioModality(modalities []string) bool {
	for _, m := range modalities {
		if strings.EqualFold(m, "audio") {
			return true
		}
	}
	return false
}

// mapVoiceToGemini maps OpenAI voice names to Gemini prebuilt voice names.
// If the voice is not a recognized OpenAI name, it's passed through as-is
// (allowing direct use of Gemini voice names like "Kore", "Puck", etc.).
func mapVoiceToGemini(voice string) string {
	switch strings.ToLower(voice) {
	case "alloy":
		return "Kore"
	case "echo":
		return "Charon"
	case "fable":
		return "Puck"
	case "onyx":
		return "Fenrir"
	case "nova":
		return "Aoede"
	case "shimmer":
		return "Leda"
	default:
		// Pass through — allows using Gemini-native voice names directly.
		return voice
	}
}

// isAudioMimeType returns true if the MIME type represents audio content.
func isAudioMimeType(mimeType string) bool {
	return strings.HasPrefix(strings.ToLower(mimeType), "audio/")
}

// audioMimeToFormat converts a Gemini audio MIME type to the OpenAI audio format string.
// Gemini TTS returns audio/L16 (PCM16) at 24000 Hz.
func audioMimeToFormat(mimeType string) string {
	lower := strings.ToLower(mimeType)
	switch {
	case strings.Contains(lower, "l16"), strings.Contains(lower, "pcm"):
		return "pcm16"
	case strings.Contains(lower, "mp3"), strings.Contains(lower, "mpeg"):
		return "mp3"
	case strings.Contains(lower, "opus"):
		return "opus"
	case strings.Contains(lower, "wav"):
		return "wav"
	case strings.Contains(lower, "flac"):
		return "flac"
	default:
		return "pcm16" // Gemini default
	}
}
