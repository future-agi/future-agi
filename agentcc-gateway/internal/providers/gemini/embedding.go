package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- Gemini embedding native types ---

type geminiEmbedRequest struct {
	Content              *geminiContent `json:"content"`
	TaskType             string         `json:"taskType,omitempty"`
	Title                string         `json:"title,omitempty"`
	OutputDimensionality *int           `json:"outputDimensionality,omitempty"`
}

type geminiEmbedResponse struct {
	Embedding *geminiEmbeddingValues `json:"embedding"`
}

type geminiEmbeddingValues struct {
	Values []float64 `json:"values"`
}

type geminiBatchEmbedRequest struct {
	Requests []geminiBatchEmbedItem `json:"requests"`
}

type geminiBatchEmbedItem struct {
	Model                string         `json:"model"`
	Content              *geminiContent `json:"content"`
	TaskType             string         `json:"taskType,omitempty"`
	OutputDimensionality *int           `json:"outputDimensionality,omitempty"`
}

type geminiBatchEmbedResponse struct {
	Embeddings []geminiEmbeddingValues `json:"embeddings"`
}

// CreateEmbedding sends an embedding request to Gemini.
// It accepts OpenAI-format requests and translates to/from Gemini's embedContent
// or batchEmbedContents endpoint.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	model := resolveModelName(req.Model)

	// Parse input: can be a single string or an array of strings.
	texts, err := parseEmbeddingInput(req.Input)
	if err != nil {
		return nil, models.ErrBadRequest("invalid_input", fmt.Sprintf("gemini: invalid embedding input: %v", err))
	}

	if len(texts) == 0 {
		return nil, models.ErrBadRequest("empty_input", "gemini: embedding input must not be empty")
	}

	// Use single embedContent for one input, batchEmbedContents for multiple.
	if len(texts) == 1 {
		return p.doSingleEmbed(ctx, model, texts[0], req.Dimensions)
	}
	return p.doBatchEmbed(ctx, model, texts, req.Dimensions)
}

// doSingleEmbed calls the embedContent endpoint for a single text.
func (p *Provider) doSingleEmbed(ctx context.Context, model string, text string, dimensions *int) (*models.EmbeddingResponse, error) {
	gemReq := geminiEmbedRequest{
		Content: &geminiContent{
			Parts: []geminiPart{{Text: text}},
		},
		OutputDimensionality: dimensions,
	}

	body, err := json.Marshal(gemReq)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: marshaling embed request: %v", err))
	}

	url := p.buildModelURL(model, "embedContent")

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: creating embed request: %v", err))
	}

	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: embed request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading embed response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseGeminiError(resp.StatusCode, respBody)
	}

	var gemResp geminiEmbedResponse
	if err := json.Unmarshal(respBody, &gemResp); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing embed response: %v", err))
	}

	return translateSingleEmbedResponse(&gemResp, model, text), nil
}

// doBatchEmbed calls the batchEmbedContents endpoint for multiple texts.
func (p *Provider) doBatchEmbed(ctx context.Context, model string, texts []string, dimensions *int) (*models.EmbeddingResponse, error) {
	// Build the Gemini model reference for batch requests.
	// Gemini requires the full model path in each batch item.
	modelRef := "models/" + model

	var items []geminiBatchEmbedItem
	for _, text := range texts {
		items = append(items, geminiBatchEmbedItem{
			Model: modelRef,
			Content: &geminiContent{
				Parts: []geminiPart{{Text: text}},
			},
			OutputDimensionality: dimensions,
		})
	}

	gemReq := geminiBatchEmbedRequest{Requests: items}

	body, err := json.Marshal(gemReq)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: marshaling batch embed request: %v", err))
	}

	url := p.buildModelURL(model, "batchEmbedContents")

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: creating batch embed request: %v", err))
	}

	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: batch embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: batch embed request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading batch embed response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseGeminiError(resp.StatusCode, respBody)
	}

	var gemResp geminiBatchEmbedResponse
	if err := json.Unmarshal(respBody, &gemResp); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing batch embed response: %v", err))
	}

	return translateBatchEmbedResponse(&gemResp, model, texts), nil
}

// --- URL and header helpers ---

// buildModelURL constructs the URL for Gemini model-scoped API calls.
// Uses the same URL patterns as buildNativeURL but adds API key for non-Vertex.
func (p *Provider) buildModelURL(model string, action string) string {
	base := p.buildNativeURL(model, action)
	if !p.vertexAI && p.apiKey != "" {
		base += "?key=" + p.apiKey
	}
	return base
}

// setAuthHeaders sets Content-Type and authentication headers for Gemini requests.
func (p *Provider) setAuthHeaders(req *http.Request) {
	req.Header.Set("Content-Type", "application/json")
	if p.vertexAI {
		if tok := p.bearerToken(); tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
	}
	// For non-Vertex, API key is passed in the URL query string.
	for k, v := range p.headers {
		req.Header.Set(k, v)
	}
}

// --- Input parsing ---

// parseEmbeddingInput extracts text strings from the OpenAI embedding input format.
// OpenAI accepts: a single string, or an array of strings (also array of ints / array of arrays of ints,
// but we only support string forms since Gemini only embeds text).
func parseEmbeddingInput(input json.RawMessage) ([]string, error) {
	if len(input) == 0 {
		return nil, fmt.Errorf("input is empty")
	}

	// Try as a single string.
	var single string
	if err := json.Unmarshal(input, &single); err == nil {
		return []string{single}, nil
	}

	// Try as an array of strings.
	var arr []string
	if err := json.Unmarshal(input, &arr); err == nil {
		return arr, nil
	}

	return nil, fmt.Errorf("input must be a string or array of strings")
}

// --- Response translation ---

// translateSingleEmbedResponse converts a Gemini embedContent response to OpenAI format.
func translateSingleEmbedResponse(resp *geminiEmbedResponse, model string, text string) *models.EmbeddingResponse {
	var data []models.EmbeddingData
	if resp.Embedding != nil {
		valuesJSON, _ := json.Marshal(resp.Embedding.Values)
		data = append(data, models.EmbeddingData{
			Object:    "embedding",
			Index:     0,
			Embedding: valuesJSON,
		})
	}

	// Gemini doesn't return token counts for embeddings.
	// Return 0 rather than a misleading estimate.
	return &models.EmbeddingResponse{
		Object: "list",
		Data:   data,
		Model:  model,
		Usage: &models.EmbeddingUsage{
			PromptTokens: 0,
			TotalTokens:  0,
		},
	}
}

// translateBatchEmbedResponse converts a Gemini batchEmbedContents response to OpenAI format.
func translateBatchEmbedResponse(resp *geminiBatchEmbedResponse, model string, texts []string) *models.EmbeddingResponse {
	var data []models.EmbeddingData
	for i, emb := range resp.Embeddings {
		valuesJSON, _ := json.Marshal(emb.Values)
		data = append(data, models.EmbeddingData{
			Object:    "embedding",
			Index:     i,
			Embedding: valuesJSON,
		})
	}

	// Gemini doesn't return token counts for embeddings.
	// Return 0 rather than a misleading estimate.
	return &models.EmbeddingResponse{
		Object: "list",
		Data:   data,
		Model:  model,
		Usage: &models.EmbeddingUsage{
			PromptTokens: 0,
			TotalTokens:  0,
		},
	}
}
