// Package twelvelabs implements an opt-in provider for the TwelveLabs video
// understanding API (https://twelvelabs.io).
//
// It exposes two video-native model families through the gateway's existing
// OpenAI-compatible surfaces, so the eval/observability platform can cover
// video models alongside text and image providers:
//
//   - Pegasus (video understanding) via ChatCompletion. The video is passed as
//     a public URL in a multimodal message; the model's analysis is returned as
//     the assistant message content.
//   - Marengo (multimodal embeddings) via CreateEmbedding. Text inputs return a
//     512-dimension embedding suitable for cross-modal retrieval against video
//     embeddings produced by the same model.
//
// The provider is entirely opt-in: it is only constructed when a provider with
// api_format "twelvelabs" is configured. With no TwelveLabs provider configured,
// gateway behavior is unchanged.
package twelvelabs

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// defaultBaseURL is the TwelveLabs REST API root. Override via provider config.
const defaultBaseURL = "https://api.twelvelabs.io/v1.3"

// defaultPegasusModel / defaultMarengoModel are used when the requested model
// does not map to a known TwelveLabs model name.
const (
	defaultPegasusModel = "pegasus1.5"
	defaultMarengoModel = "marengo3.0"
)

// Provider implements the gateway Provider and EmbeddingProvider interfaces for
// the TwelveLabs API.
type Provider struct {
	id         string
	baseURL    string
	apiKey     string
	httpClient *http.Client
	semaphore  chan struct{}
	headers    map[string]string
}

// New creates a new TwelveLabs provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	timeout := cfg.DefaultTimeout
	if timeout == 0 {
		// Pegasus analysis can take a while on longer clips; allow generous time.
		timeout = 120 * time.Second
	}

	maxConcurrent := cfg.MaxConcurrent
	if maxConcurrent == 0 {
		maxConcurrent = 100
	}

	poolSize := cfg.ConnPoolSize
	if poolSize == 0 {
		poolSize = 100
	}

	baseURL := cfg.BaseURL
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}

	return &Provider{
		id:      id,
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  cfg.APIKey,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   timeout,
		},
		semaphore: make(chan struct{}, maxConcurrent),
		headers:   cfg.Headers,
	}, nil
}

func (p *Provider) ID() string { return p.id }

func (p *Provider) acquireSemaphore(ctx context.Context) error {
	select {
	case p.semaphore <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (p *Provider) releaseSemaphore() {
	<-p.semaphore
}

// setHeaders applies authentication and any configured extra headers.
// TwelveLabs authenticates with an "x-api-key" header.
func (p *Provider) setHeaders(req *http.Request) {
	if p.apiKey != "" {
		req.Header.Set("x-api-key", p.apiKey)
	}
	for k, v := range p.headers {
		req.Header.Set(k, v)
	}
}

// ChatCompletion runs Pegasus video understanding. The request must contain a
// video URL in a multimodal message (an "image_url"/"video_url" content part or
// a bare http(s) URL) and a text prompt. The analysis text is returned as the
// assistant message.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("twelvelabs: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	ar, err := translateAnalyzeRequest(req)
	if err != nil {
		return nil, err
	}

	body, err := json.Marshal(ar)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: marshaling analyze request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/analyze", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: creating analyze request: %v", err))
	}
	httpReq.Header.Set("Content-Type", "application/json")
	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("twelvelabs: analyze request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("twelvelabs: analyze request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("twelvelabs: reading analyze response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseError(resp.StatusCode, respBody)
	}

	var ar2 analyzeResponse
	if err := json.Unmarshal(respBody, &ar2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("twelvelabs: parsing analyze response: %v", err))
	}

	return translateAnalyzeResponse(&ar2, ar.ModelName), nil
}

// StreamChatCompletion adapts the non-streaming Pegasus analyze call into a
// single-chunk stream. The TwelveLabs /analyze endpoint used here is synchronous,
// so we emit the full result as one delta followed by a stop chunk.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 2)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		resp, err := p.ChatCompletion(ctx, req)
		if err != nil {
			errs <- err
			return
		}

		var content string
		if len(resp.Choices) > 0 {
			// Choice.Message.Content is raw JSON (a JSON string); unwrap it.
			_ = json.Unmarshal(resp.Choices[0].Message.Content, &content)
		}

		role := "assistant"
		stop := "stop"
		select {
		case chunks <- models.StreamChunk{
			ID:      resp.ID,
			Object:  "chat.completion.chunk",
			Created: resp.Created,
			Model:   resp.Model,
			Choices: []models.StreamChoice{{
				Index: 0,
				Delta: models.Delta{Role: role, Content: &content},
			}},
		}:
		case <-ctx.Done():
			return
		}

		select {
		case chunks <- models.StreamChunk{
			ID:      resp.ID,
			Object:  "chat.completion.chunk",
			Created: resp.Created,
			Model:   resp.Model,
			Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{}, FinishReason: &stop}},
			Usage:   resp.Usage,
		}:
		case <-ctx.Done():
		}
	}()

	return chunks, errs
}

// CreateEmbedding runs Marengo multimodal embedding for text input. Each text
// input is embedded independently and returned in OpenAI embedding shape.
// Marengo returns 512-dimension float vectors.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("twelvelabs: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	texts, err := parseEmbeddingInput(req.Input)
	if err != nil {
		return nil, err
	}
	if len(texts) == 0 {
		return nil, models.ErrBadRequest("invalid_request_error", "twelvelabs: embedding input is empty")
	}

	model := resolveModelName(req.Model)
	if model == "" || !strings.HasPrefix(model, "marengo") {
		model = defaultMarengoModel
	}

	resp := &models.EmbeddingResponse{Object: "list", Model: model}

	// The TwelveLabs /embed endpoint embeds one text per request, so iterate.
	for i, text := range texts {
		vec, err := p.embedText(ctx, model, text)
		if err != nil {
			return nil, err
		}
		embJSON, err := json.Marshal(vec)
		if err != nil {
			return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: marshaling embedding: %v", err))
		}
		resp.Data = append(resp.Data, models.EmbeddingData{
			Object:    "embedding",
			Index:     i,
			Embedding: embJSON,
		})
	}

	return resp, nil
}

// embedText performs a single multipart /embed call and returns the float vector.
// The TwelveLabs /embed endpoint requires multipart/form-data for every request,
// including text-only ones.
func (p *Provider) embedText(ctx context.Context, model, text string) ([]float64, error) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	if err := mw.WriteField("model_name", model); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: writing embed form: %v", err))
	}
	if err := mw.WriteField("text", text); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: writing embed form: %v", err))
	}
	if err := mw.Close(); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: closing embed form: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/embed", &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("twelvelabs: creating embed request: %v", err))
	}
	httpReq.Header.Set("Content-Type", mw.FormDataContentType())
	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("twelvelabs: embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("twelvelabs: embed request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("twelvelabs: reading embed response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseError(resp.StatusCode, respBody)
	}

	var er embedResponse
	if err := json.Unmarshal(respBody, &er); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("twelvelabs: parsing embed response: %v", err))
	}

	vec := er.firstFloatVector()
	if len(vec) == 0 {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, "twelvelabs: embed response contained no embedding")
	}
	return vec, nil
}

// ListModels returns the configured TwelveLabs models. The TwelveLabs API has no
// general model-listing endpoint, so models come from provider config.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}
