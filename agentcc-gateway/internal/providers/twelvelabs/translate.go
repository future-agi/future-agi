package twelvelabs

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- Pegasus /analyze types ---

// videoContext is the TwelveLabs video reference. For public URLs use
// {"type":"url","url":...}; for previously uploaded assets use
// {"type":"asset","asset_id":...}.
type videoContext struct {
	Type    string `json:"type"`
	URL     string `json:"url,omitempty"`
	AssetID string `json:"asset_id,omitempty"`
}

type analyzeRequest struct {
	ModelName   string       `json:"model_name"`
	Video       videoContext `json:"video"`
	Prompt      string       `json:"prompt"`
	MaxTokens   int          `json:"max_tokens,omitempty"`
	Temperature *float64     `json:"temperature,omitempty"`
}

type analyzeResponse struct {
	ID    string `json:"id"`
	Data  string `json:"data"`
	Usage *struct {
		OutputTokens int `json:"output_tokens"`
	} `json:"usage,omitempty"`
}

// translateAnalyzeRequest builds a Pegasus analyze request from an OpenAI-style
// chat completion request. It extracts a video URL (or asset id) and the text
// prompt from the messages.
func translateAnalyzeRequest(req *models.ChatCompletionRequest) (*analyzeRequest, error) {
	model := resolveModelName(req.Model)
	if model == "" || !strings.HasPrefix(model, "pegasus") {
		model = defaultPegasusModel
	}

	video, prompt, err := extractVideoAndPrompt(req.Messages)
	if err != nil {
		return nil, err
	}

	// Pegasus 1.5 requires max_tokens between 512 and 98304. The OpenAI default
	// is much smaller, so clamp up to the model's minimum when a value is set.
	maxTokens := 2048
	if req.MaxTokens != nil && *req.MaxTokens > 0 {
		maxTokens = *req.MaxTokens
	} else if req.MaxCompletionTokens != nil && *req.MaxCompletionTokens > 0 {
		maxTokens = *req.MaxCompletionTokens
	}
	if strings.HasPrefix(model, "pegasus1.5") && maxTokens < 512 {
		maxTokens = 512
	}

	return &analyzeRequest{
		ModelName:   model,
		Video:       video,
		Prompt:      prompt,
		MaxTokens:   maxTokens,
		Temperature: req.Temperature,
	}, nil
}

// extractVideoAndPrompt pulls a video reference and the text prompt out of the
// chat messages. A video may be supplied as:
//   - a multimodal content part with type "video_url" or "image_url" whose URL
//     points at a video, or
//   - a TwelveLabs asset id via {"type":"input_asset","asset_id":...}, or
//   - a bare http(s) URL in a text part.
//
// The prompt is the concatenation of all text parts across user messages.
func extractVideoAndPrompt(messages []models.Message) (videoContext, string, error) {
	var video videoContext
	var promptParts []string

	for _, msg := range messages {
		if msg.Role != "user" && msg.Role != "system" {
			continue
		}
		if len(msg.Content) == 0 {
			continue
		}

		// Content may be a plain string or an array of content parts.
		var asString string
		if err := json.Unmarshal(msg.Content, &asString); err == nil {
			if u := firstURL(asString); u != "" && video.URL == "" && video.AssetID == "" {
				video = videoContext{Type: "url", URL: u}
				asString = strings.TrimSpace(strings.Replace(asString, u, "", 1))
			}
			if asString != "" {
				promptParts = append(promptParts, asString)
			}
			continue
		}

		var parts []contentPart
		if err := json.Unmarshal(msg.Content, &parts); err != nil {
			continue
		}
		for _, part := range parts {
			switch part.Type {
			case "text":
				if part.Text != "" {
					promptParts = append(promptParts, part.Text)
				}
			case "video_url", "image_url":
				url := part.urlValue()
				if url != "" && video.URL == "" && video.AssetID == "" {
					video = videoContext{Type: "url", URL: url}
				}
			case "input_asset", "asset":
				if part.AssetID != "" && video.URL == "" && video.AssetID == "" {
					video = videoContext{Type: "asset", AssetID: part.AssetID}
				}
			}
		}
	}

	if video.URL == "" && video.AssetID == "" {
		return video, "", models.ErrBadRequest("invalid_request_error",
			"twelvelabs: no video found in request. Provide a public video URL (image_url/video_url content part or a bare http(s) URL) "+
				"or a TwelveLabs asset id. Note: direct asset upload is capped at 200MB; public URLs support up to 4GB.")
	}
	if strings.TrimSpace(strings.Join(promptParts, " ")) == "" {
		return video, "", models.ErrBadRequest("invalid_request_error", "twelvelabs: no text prompt found in request messages")
	}

	return video, strings.TrimSpace(strings.Join(promptParts, " ")), nil
}

// contentPart is a subset of an OpenAI multimodal content part.
type contentPart struct {
	Type     string          `json:"type"`
	Text     string          `json:"text,omitempty"`
	ImageURL json.RawMessage `json:"image_url,omitempty"`
	VideoURL json.RawMessage `json:"video_url,omitempty"`
	AssetID  string          `json:"asset_id,omitempty"`
}

// urlValue returns the URL from an image_url/video_url part, which may be either
// a bare string or an object {"url": "..."}.
func (c contentPart) urlValue() string {
	raw := c.VideoURL
	if len(raw) == 0 {
		raw = c.ImageURL
	}
	if len(raw) == 0 {
		return ""
	}
	var asString string
	if err := json.Unmarshal(raw, &asString); err == nil {
		return asString
	}
	var obj struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal(raw, &obj); err == nil {
		return obj.URL
	}
	return ""
}

// firstURL returns the first http(s) token in s, or "".
func firstURL(s string) string {
	for _, tok := range strings.Fields(s) {
		if strings.HasPrefix(tok, "http://") || strings.HasPrefix(tok, "https://") {
			return tok
		}
	}
	return ""
}

// translateAnalyzeResponse maps a Pegasus analyze result into an OpenAI chat
// completion response.
func translateAnalyzeResponse(resp *analyzeResponse, model string) *models.ChatCompletionResponse {
	contentJSON, _ := json.Marshal(resp.Data)

	out := &models.ChatCompletionResponse{
		ID:      chatID(resp.ID),
		Object:  "chat.completion",
		Created: 0,
		Model:   model,
		Choices: []models.Choice{{
			Index: 0,
			Message: models.Message{
				Role:    "assistant",
				Content: contentJSON,
			},
			FinishReason: "stop",
		}},
	}
	if resp.Usage != nil {
		out.Usage = &models.Usage{
			CompletionTokens: resp.Usage.OutputTokens,
			TotalTokens:      resp.Usage.OutputTokens,
		}
	}
	return out
}

func chatID(id string) string {
	if id == "" {
		return "chatcmpl-twelvelabs"
	}
	return "chatcmpl-" + id
}

// --- Marengo /embed types ---

type embedResponse struct {
	ModelName     string `json:"model_name"`
	TextEmbedding *struct {
		Segments []embedSegment `json:"segments"`
	} `json:"text_embedding,omitempty"`
}

type embedSegment struct {
	// The TwelveLabs REST API names the vector field "float" (the Python SDK
	// aliases it to "float_").
	Float []float64 `json:"float"`
}

// firstFloatVector returns the first segment's float vector, or nil.
func (e *embedResponse) firstFloatVector() []float64 {
	if e.TextEmbedding == nil || len(e.TextEmbedding.Segments) == 0 {
		return nil
	}
	return e.TextEmbedding.Segments[0].Float
}

// parseEmbeddingInput parses an OpenAI embedding input (string or []string).
func parseEmbeddingInput(input json.RawMessage) ([]string, error) {
	if len(input) == 0 {
		return nil, nil
	}
	var single string
	if err := json.Unmarshal(input, &single); err == nil {
		return []string{single}, nil
	}
	var many []string
	if err := json.Unmarshal(input, &many); err == nil {
		return many, nil
	}
	return nil, models.ErrBadRequest("invalid_request_error", "twelvelabs: embedding input must be a string or array of strings")
}

// --- shared helpers ---

// resolveModelName strips a "provider/" prefix if present.
func resolveModelName(model string) string {
	if idx := strings.Index(model, "/"); idx > 0 {
		return model[idx+1:]
	}
	return model
}

// parseError maps a TwelveLabs error envelope to an APIError. The envelope is
// {"code": "...", "message": "..."}.
func parseError(status int, body []byte) *models.APIError {
	var errResp struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Message != "" {
		switch {
		case status == http.StatusUnauthorized:
			return models.ErrUnauthorized("twelvelabs: " + errResp.Message)
		case status == http.StatusTooManyRequests:
			return models.ErrTooManyRequests("twelvelabs: " + errResp.Message)
		case status >= 400 && status < 500:
			code := errResp.Code
			if code == "" {
				code = "invalid_request_error"
			}
			return models.ErrBadRequest(code, "twelvelabs: "+errResp.Message)
		default:
			return models.ErrUpstreamProvider(status, "twelvelabs: "+errResp.Message)
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("twelvelabs error (HTTP %d): %s", status, msg))
}
