package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// maxImagesPerRequest bounds n: Gemini returns one image per generateContent
// call, so each image is one upstream call.
const maxImagesPerRequest = 10

// --- Gemini image output (generateContent) ---

// geminiImageRequest is a generateContent request asking for image output,
// the way Gemini image models (gemini-3.1-flash-image, gemini-3-pro-image,
// the Nano Banana family) generate images.
type geminiImageRequest struct {
	Contents         []geminiContent        `json:"contents"`
	GenerationConfig geminiImageGenerConfig `json:"generationConfig"`
}

type geminiImageGenerConfig struct {
	ResponseModalities []string           `json:"responseModalities"`
	ImageConfig        *geminiImageConfig `json:"imageConfig,omitempty"`
}

type geminiImageConfig struct {
	AspectRatio string `json:"aspectRatio,omitempty"`
}

// --- Vertex Imagen (predict) ---

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

// CreateImage generates images for an OpenAI-format request. Gemini image
// models are called through generateContent with image output, one call per
// image, made one after another: the request succeeds only if every call
// does, so when call k fails the k-1 images already generated (and billed by
// Google) are not returned. Imagen models are served only through Vertex
// AI's predict endpoint (the Gemini API does not serve Imagen); like every
// provider endpoint it counts as qualified only once a live qualification
// run passes it (the capability matrix's qualified flag). An n of 0 or
// less, or none, is one image.
func (p *Provider) CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error) {
	n := 1
	if req.N != nil && *req.N > 0 {
		n = *req.N
	}
	if n > maxImagesPerRequest {
		return nil, models.ErrBadRequest("invalid_n", fmt.Sprintf("n must be at most %d", maxImagesPerRequest))
	}
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	model := resolveModelName(req.Model)
	if p.vertexAI && isImagenModel(model) {
		return p.predictImages(ctx, model, req, n)
	}
	out := &models.ImageResponse{Created: time.Now().Unix()}
	for i := 0; i < n; i++ {
		img, err := p.generateImage(ctx, model, req)
		if err != nil {
			return nil, err
		}
		out.Data = append(out.Data, img)
	}
	return out, nil
}

// generateImage makes one generateContent call for one image.
func (p *Provider) generateImage(ctx context.Context, model string, req *models.ImageRequest) (models.ImageData, error) {
	gemReq := geminiImageRequest{
		Contents:         []geminiContent{{Role: "user", Parts: []geminiPart{{Text: req.Prompt}}}},
		GenerationConfig: geminiImageGenerConfig{ResponseModalities: []string{"IMAGE"}},
	}
	if req.Size != "" {
		gemReq.GenerationConfig.ImageConfig = &geminiImageConfig{AspectRatio: mapSizeToAspectRatio(req.Size)}
	}
	var gemResp geminiResponse
	if err := p.postImage(ctx, p.buildModelURL(model, "generateContent"), gemReq, &gemResp); err != nil {
		return models.ImageData{}, err
	}
	for _, c := range gemResp.Candidates {
		for _, part := range c.Content.Parts {
			if part.InlineData != nil && part.InlineData.Data != "" && strings.HasPrefix(part.InlineData.MimeType, "image/") {
				return models.ImageData{B64JSON: part.InlineData.Data}, nil
			}
		}
	}
	reason := "none"
	if len(gemResp.Candidates) > 0 && gemResp.Candidates[0].FinishReason != "" {
		reason = gemResp.Candidates[0].FinishReason
	}
	return models.ImageData{}, &models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Code: "no_image_returned",
		Message: "gemini: the response has no image (finish reason " + reason + ")"}
}

// predictImages calls Vertex AI's Imagen predict endpoint for n images.
func (p *Provider) predictImages(ctx context.Context, model string, req *models.ImageRequest, n int) (*models.ImageResponse, error) {
	gemReq := geminiPredictRequest{
		Instances:  []geminiPredictInstance{{Prompt: req.Prompt}},
		Parameters: &geminiPredictParameters{SampleCount: n},
	}
	if req.Size != "" {
		gemReq.Parameters.AspectRatio = mapSizeToAspectRatio(req.Size)
	}
	var gemResp geminiPredictResponse
	if err := p.postImage(ctx, p.buildModelURL(model, "predict"), gemReq, &gemResp); err != nil {
		return nil, err
	}
	return translateImageResponse(&gemResp), nil
}

// postImage POSTs body to endpoint and decodes a 200 answer into out.
func (p *Provider) postImage(ctx context.Context, endpoint string, body, out any) error {
	payload, err := json.Marshal(body)
	if err != nil {
		return models.ErrInternal(fmt.Sprintf("gemini: marshaling image request: %v", err))
	}
	httpReq, err := http.NewRequestWithContext(ctx, "POST", endpoint, bytes.NewReader(payload))
	if err != nil {
		return models.ErrInternal("gemini: creating image request: " + withoutURL(err))
	}
	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return models.ErrGatewayTimeout("gemini: image request timed out")
		}
		return models.ErrUpstreamProvider(0, "gemini: image request failed: "+withoutURL(err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 50*1024*1024)) // 50MB — images can be large.
	if err != nil {
		return common.ReadError(err, resp.StatusCode, "gemini: reading image response")
	}
	if resp.StatusCode != http.StatusOK {
		if resp.StatusCode == http.StatusUnauthorized && p.tokenProvider != nil {
			p.tokenProvider.Invalidate(sentToken(httpReq))
		}
		return parseGeminiError(resp.StatusCode, respBody)
	}
	if err := json.Unmarshal(respBody, out); err != nil {
		return models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing image response: %v", err))
	}
	return nil
}

// withoutURL is err's text without the request URL it names. The API key
// travels in the x-goog-api-key header (setAPIKey), never in the URL; a
// *url.Error still prints the whole URL, so leaving it out keeps the
// upstream's address out of the client's error and the logs as well.
func withoutURL(err error) string {
	var ue *url.Error
	if errors.As(err, &ue) && ue.Err != nil {
		return ue.Op + ": " + ue.Err.Error()
	}
	return err.Error()
}

// isImagenModel reports whether model is an Imagen model, which only Vertex
// AI's predict endpoint serves.
func isImagenModel(model string) bool {
	return strings.HasPrefix(strings.ToLower(model), "imagen-")
}

// --- Response translation ---

// translateImageResponse converts a Vertex Imagen predict response to OpenAI ImageResponse format.
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
// OpenAI sizes: "256x256", "512x512", "1024x1024", "1792x1024", "1024x1792",
// "1536x1024", "1024x1536"
// Gemini aspect ratios: "1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"
func mapSizeToAspectRatio(size string) string {
	switch size {
	case "256x256", "512x512", "1024x1024":
		return "1:1"
	case "1792x1024":
		return "16:9"
	case "1024x1792":
		return "9:16"
	case "1536x1024":
		return "3:2"
	case "1024x1536":
		return "2:3"
	default:
		return "1:1" // safe default
	}
}
