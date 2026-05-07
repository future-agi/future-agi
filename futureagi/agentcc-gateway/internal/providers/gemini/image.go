package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- Gemini Imagen native types ---

type geminiPredictRequest struct {
	Instances  []geminiPredictInstance  `json:"instances"`
	Parameters *geminiPredictParameters `json:"parameters,omitempty"`
}

type geminiPredictInstance struct {
	Prompt string `json:"prompt"`
}

type geminiPredictParameters struct {
	SampleCount      int    `json:"sampleCount,omitempty"`
	AspectRatio      string `json:"aspectRatio,omitempty"`
	PersonGeneration string `json:"personGeneration,omitempty"`
}

type geminiPredictResponse struct {
	Predictions []geminiPrediction `json:"predictions"`
}

type geminiPrediction struct {
	BytesBase64Encoded string `json:"bytesBase64Encoded"`
	MimeType           string `json:"mimeType"`
}

// CreateImage sends an image generation request to Gemini's Imagen API.
// It accepts OpenAI-format requests and translates to/from Gemini's predict endpoint.
func (p *Provider) CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	model := resolveModelName(req.Model)

	// Build the Gemini predict request.
	gemReq := geminiPredictRequest{
		Instances: []geminiPredictInstance{
			{Prompt: req.Prompt},
		},
	}

	// Map OpenAI parameters to Gemini parameters.
	params := &geminiPredictParameters{}
	hasParams := false

	if req.N != nil && *req.N > 0 {
		params.SampleCount = *req.N
		hasParams = true
	} else {
		// Default to 1 image.
		params.SampleCount = 1
		hasParams = true
	}

	// Map OpenAI size to Gemini aspect ratio.
	if req.Size != "" {
		if ar := mapSizeToAspectRatio(req.Size); ar != "" {
			params.AspectRatio = ar
			hasParams = true
		}
	}

	if hasParams {
		gemReq.Parameters = params
	}

	body, err := json.Marshal(gemReq)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: marshaling image request: %v", err))
	}

	url := p.buildModelURL(model, "predict")

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: creating image request: %v", err))
	}

	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: image request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: image request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 50*1024*1024)) // 50MB — images can be large.
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading image response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseGeminiError(resp.StatusCode, respBody)
	}

	var gemResp geminiPredictResponse
	if err := json.Unmarshal(respBody, &gemResp); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing image response: %v", err))
	}

	return translateImageResponse(&gemResp), nil
}

// --- Response translation ---

// translateImageResponse converts a Gemini predict response to OpenAI ImageResponse format.
func translateImageResponse(resp *geminiPredictResponse) *models.ImageResponse {
	result := &models.ImageResponse{
		Created: time.Now().Unix(),
	}

	for _, pred := range resp.Predictions {
		result.Data = append(result.Data, models.ImageData{
			B64JSON: pred.BytesBase64Encoded,
		})
	}

	return result
}

// mapSizeToAspectRatio converts OpenAI image size strings to Gemini aspect ratios.
// OpenAI sizes: "256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"
// Gemini aspect ratios: "1:1", "3:4", "4:3", "9:16", "16:9"
func mapSizeToAspectRatio(size string) string {
	switch size {
	case "256x256", "512x512", "1024x1024":
		return "1:1"
	case "1792x1024":
		return "16:9"
	case "1024x1792":
		return "9:16"
	default:
		return "1:1" // safe default
	}
}
