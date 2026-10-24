package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// --- Gemini embedding native types ---

type geminiEmbedRequest struct {
	Content              *geminiContent `json:"content"`
	TaskType             string         `json:"taskType,omitempty"`
	Title                string         `json:"title,omitempty"`
	OutputDimensionality *int           `json:"outputDimensionality,omitempty"`
}

type geminiEmbedResponse struct {
	Embedding     *geminiEmbeddingValues `json:"embedding"`
	UsageMetadata *geminiEmbedUsage      `json:"usageMetadata,omitempty"`
}

// geminiEmbedUsage is the usage an embed response may report.
type geminiEmbedUsage struct {
	PromptTokenCount int `json:"promptTokenCount"`
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
	Embeddings    []geminiEmbeddingValues `json:"embeddings"`
	UsageMetadata *geminiEmbedUsage       `json:"usageMetadata,omitempty"`
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
	taskType := geminiTaskTypes[req.InputType]
	if len(texts) == 1 {
		return p.doSingleEmbed(ctx, model, texts[0], req.Dimensions, taskType)
	}
	return p.doBatchEmbed(ctx, model, texts, req.Dimensions, taskType)
}

// geminiTaskTypes maps a request's input_type to Gemini's taskType. A
// request that names none sends no taskType.
var geminiTaskTypes = map[string]string{
	models.EmbeddingInputTypeQuery:          "RETRIEVAL_QUERY",
	models.EmbeddingInputTypeDocument:       "RETRIEVAL_DOCUMENT",
	models.EmbeddingInputTypeClassification: "CLASSIFICATION",
	models.EmbeddingInputTypeClustering:     "CLUSTERING",
}

// AcceptsEmbeddingInputType reports that an embed request's input_type is
// sent per request (Gemini's taskType).
func (p *Provider) AcceptsEmbeddingInputType() bool { return true }

// EstimateEmbeddingUsage is a token estimate for embedding req's input, for
// a response whose upstream reported no usage, so a paid call is never
// costed at $0: about 4 characters (or 1.3 words) a token, at least one per
// input. It is nil for an input the adapter cannot embed.
func (p *Provider) EstimateEmbeddingUsage(req *models.EmbeddingRequest) *models.EmbeddingUsage {
	texts, err := parseEmbeddingInput(req.Input)
	if err != nil || len(texts) == 0 {
		return nil
	}
	n := 0
	for _, text := range texts {
		n += max(estimateTextTokens(text), 1)
	}
	return &models.EmbeddingUsage{PromptTokens: n, TotalTokens: n}
}

// estimateTextTokens is the larger of ~4 characters a token and ~1.3 tokens
// a word, the heuristic the tokenizer package uses for chat.
func estimateTextTokens(s string) int {
	return max((len(s)+3)/4, len(strings.Fields(s))*13/10)
}

// doSingleEmbed calls the embedContent endpoint for a single text.
func (p *Provider) doSingleEmbed(ctx context.Context, model string, text string, dimensions *int, taskType string) (*models.EmbeddingResponse, error) {
	gemReq := geminiEmbedRequest{
		Content: &geminiContent{
			Parts: []geminiPart{{Text: text}},
		},
		TaskType:             taskType,
		OutputDimensionality: dimensions,
	}

	body, err := json.Marshal(gemReq)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: marshaling embed request: %v", err))
	}

	url := p.buildModelURL(model, "embedContent")

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal("gemini: creating embed request: " + withoutURL(err))
	}

	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, "gemini: embed request failed: "+withoutURL(err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "gemini: reading embed response")
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
func (p *Provider) doBatchEmbed(ctx context.Context, model string, texts []string, dimensions *int, taskType string) (*models.EmbeddingResponse, error) {
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
			TaskType:             taskType,
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
		return nil, models.ErrInternal("gemini: creating batch embed request: " + withoutURL(err))
	}

	p.setAuthHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: batch embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, "gemini: batch embed request failed: "+withoutURL(err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "gemini: reading batch embed response")
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
	return p.buildNativeURL(model, action)
}

// setAuthHeaders sets Content-Type and authentication headers for Gemini requests.
func (p *Provider) setAuthHeaders(req *http.Request) {
	req.Header.Set("Content-Type", "application/json")
	p.setAPIKey(req)
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

	return &models.EmbeddingResponse{
		Object: "list",
		Data:   data,
		Model:  model,
		Usage:  reportedEmbedUsage(resp.UsageMetadata),
	}
}

// reportedEmbedUsage is the usage an embed response reported, nil when it
// reported none (or a zero count): the handler then asks for
// EstimateEmbeddingUsage instead of costing the call at $0.
func reportedEmbedUsage(u *geminiEmbedUsage) *models.EmbeddingUsage {
	if u == nil || u.PromptTokenCount <= 0 {
		return nil
	}
	return &models.EmbeddingUsage{PromptTokens: u.PromptTokenCount, TotalTokens: u.PromptTokenCount}
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

	return &models.EmbeddingResponse{
		Object: "list",
		Data:   data,
		Model:  model,
		Usage:  reportedEmbedUsage(resp.UsageMetadata),
	}
}
