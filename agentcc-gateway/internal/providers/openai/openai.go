package openai

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider implements the Provider interface for OpenAI-compatible APIs.
type Provider struct {
	id         string
	baseURL    string
	apiKey     string
	httpClient *http.Client
	semaphore  chan struct{}
}

// New creates a new OpenAI-compatible provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	timeout := cfg.DefaultTimeout
	if timeout == 0 {
		timeout = 60 * time.Second
	}

	maxConcurrent := cfg.MaxConcurrent
	if maxConcurrent == 0 {
		maxConcurrent = 100
	}

	poolSize := cfg.ConnPoolSize
	if poolSize == 0 {
		poolSize = 100
	}

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}
	if cfg.SkipTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // user-opted skip
		slog.Warn("TLS verification disabled for provider", "provider", id)
	}

	return &Provider{
		id:      id,
		baseURL: strings.TrimRight(cfg.BaseURL, "/"),
		apiKey:  cfg.APIKey,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   timeout,
		},
		semaphore: make(chan struct{}, maxConcurrent),
	}, nil
}

// endpoint builds a full upstream URL for a versioned path like "/v1/ocr".
// If the configured baseURL already ends with "/v1" (as some presets do,
// e.g. "https://api.cohere.ai/compatibility/v1"), the leading "/v1" of path
// is stripped to avoid "/v1/v1/..." — keeps callers from having to reason
// about whether each provider preset includes the version suffix.
func (p *Provider) endpoint(path string) string {
	if strings.HasSuffix(p.baseURL, "/v1") && strings.HasPrefix(path, "/v1/") {
		return p.baseURL + path[len("/v1"):]
	}
	return p.baseURL + path
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

// OCR forwards a /v1/ocr request to the provider's upstream. Used for
// Mistral (which serves POST https://api.mistral.ai/v1/ocr) when aliased to
// the openai-compat provider via the mistral preset. Returns the response
// body parsed into the shared OCRResponse shape.
func (p *Provider) OCR(ctx context.Context, req *models.OCRRequest) (*models.OCRResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	body, err := json.Marshal(req)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/ocr"), bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating request: %v", err))
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 25*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading provider response: %v", err))
	}
	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var out models.OCRResponse
	if err := json.Unmarshal(respBody, &out); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing provider response: %v", err))
	}
	return &out, nil
}

// TextCompletion sends a legacy /v1/completions request upstream, passing the
// body through verbatim. This is required for legacy-only models like
// gpt-3.5-turbo-instruct, davinci-002, babbage-002 — they reject requests to
// /v1/chat/completions with the "not a chat model" error.
func (p *Provider) TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	reqCopy := *req
	reqCopy.Stream = false

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/completions"), bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading provider response: %v", err))
	}
	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.CompletionResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing provider response: %v", err))
	}
	return &result, nil
}

// ChatCompletion sends a non-streaming chat completion request.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	// Strip provider prefix from model name.
	actualModel := resolveModelName(req.Model)

	// Create a copy with the resolved model name.
	reqCopy := *req
	reqCopy.Model = actualModel
	reqCopy.Stream = false

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024)) // 10MB limit
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading provider response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.ChatCompletionResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing provider response: %v", err))
	}

	return &result, nil
}

// StreamChatCompletion sends a streaming chat completion request.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- models.ErrGatewayTimeout("provider concurrency limit reached")
			return
		}
		defer p.releaseSemaphore()

		actualModel := resolveModelName(req.Model)
		reqCopy := *req
		reqCopy.Model = actualModel
		reqCopy.Stream = true

		body, err := json.Marshal(&reqCopy)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
			return
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/chat/completions", bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("creating request: %v", err))
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		if p.apiKey != "" {
			httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
		}

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("provider request timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("provider error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
				return
			}
			errs <- parseProviderError(resp.StatusCode, respBody)
			return
		}

		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

		for scanner.Scan() {
			line := scanner.Text()

			if line == "" {
				continue
			}

			if !strings.HasPrefix(line, "data: ") {
				continue
			}

			data := strings.TrimPrefix(line, "data: ")

			if data == "[DONE]" {
				return
			}

			var chunk models.StreamChunk
			if err := json.Unmarshal([]byte(data), &chunk); err != nil {
				slog.Warn("failed to parse stream chunk",
					"error", err,
					"data", data,
					"provider", p.id,
				)
				continue
			}

			select {
			case chunks <- chunk:
			case <-ctx.Done():
				return
			}
		}

		if err := scanner.Err(); err != nil {
			if ctx.Err() == nil {
				errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("stream read error: %v", err))
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models from this provider.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	httpReq, err := http.NewRequestWithContext(ctx, "GET", p.baseURL+"/v1/models", nil)
	if err != nil {
		return nil, err
	}

	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("list models returned status %d", resp.StatusCode)
	}

	var result models.ModelListResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return result.Data, nil
}

// CreateImage sends an image generation request (DALL-E).
func (p *Provider) CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling image request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/images/generations", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating image request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("image request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("image request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading image response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.ImageResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing image response: %v", err))
	}

	return &result, nil
}

// CreateSpeech sends a text-to-speech request and returns an audio stream.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("marshaling speech request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/audio/speech", bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, "", models.ErrGatewayTimeout("speech request timed out")
		}
		return nil, "", models.ErrUpstreamProvider(0, fmt.Sprintf("speech request failed: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if readErr != nil {
			return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
				fmt.Sprintf("speech error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
		}
		return nil, "", parseProviderError(resp.StatusCode, respBody)
	}

	contentType := resp.Header.Get("Content-Type")
	if contentType == "" {
		contentType = "audio/mpeg"
	}

	// Wrap the body to release semaphore on close.
	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, contentType, nil
}

// semaphoreReadCloser wraps an io.ReadCloser to release a semaphore on Close.
type semaphoreReadCloser struct {
	io.ReadCloser
	release func()
}

func (s *semaphoreReadCloser) Close() error {
	err := s.ReadCloser.Close()
	s.release()
	return err
}

// CreateTranscription sends an audio transcription request (Whisper).
func (p *Provider) CreateTranscription(ctx context.Context, req *models.TranscriptionRequest) (*models.TranscriptionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	// Build multipart form.
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)

	// Add file field.
	fileName := req.FileName
	if fileName == "" {
		fileName = "audio.wav"
	}
	filePart, err := writer.CreateFormFile("file", fileName)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating multipart file: %v", err))
	}
	if _, err := filePart.Write(req.FileData); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("writing file data: %v", err))
	}

	// Add model field.
	writer.WriteField("model", resolveModelName(req.Model))

	// Add optional fields.
	if req.Language != "" {
		writer.WriteField("language", req.Language)
	}
	if req.ResponseFormat != "" {
		writer.WriteField("response_format", req.ResponseFormat)
	}
	if req.Temperature != nil {
		writer.WriteField("temperature", fmt.Sprintf("%g", *req.Temperature))
	}

	if err := writer.Close(); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("closing multipart writer: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/audio/transcriptions", &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating transcription request: %v", err))
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("transcription request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("transcription request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading transcription response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.TranscriptionResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing transcription response: %v", err))
	}

	return &result, nil
}

// CreateResponse sends a Responses API request and returns the raw JSON response.
func (p *Provider) CreateResponse(ctx context.Context, reqBody []byte) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/responses", bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating responses request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("responses request timed out")
		}
		return nil, 0, models.ErrUpstreamProvider(0, fmt.Sprintf("responses request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading responses body: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, resp.StatusCode, parseProviderError(resp.StatusCode, respBody)
	}

	return respBody, resp.StatusCode, nil
}

// StreamResponse sends a streaming Responses API request and returns the SSE stream.
func (p *Provider) StreamResponse(ctx context.Context, reqBody []byte) (io.ReadCloser, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/responses", bytes.NewReader(reqBody))
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating streaming responses request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "text/event-stream")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("streaming responses request timed out")
		}
		return nil, 0, models.ErrUpstreamProvider(0, fmt.Sprintf("streaming responses request failed: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if readErr != nil {
			return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode,
				fmt.Sprintf("responses error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
		}
		return nil, resp.StatusCode, parseProviderError(resp.StatusCode, respBody)
	}

	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, resp.StatusCode, nil
}

// CreateTranslation sends an audio translation request (Whisper — translates to English).
func (p *Provider) CreateTranslation(ctx context.Context, req *models.TranslationRequest) (*models.TranslationResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	// Build multipart form.
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)

	// Add file field.
	fileName := req.FileName
	if fileName == "" {
		fileName = "audio.wav"
	}
	filePart, err := writer.CreateFormFile("file", fileName)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating multipart file: %v", err))
	}
	if _, err := filePart.Write(req.FileData); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("writing file data: %v", err))
	}

	// Add model field.
	writer.WriteField("model", resolveModelName(req.Model))

	// Add optional fields.
	if req.Prompt != "" {
		writer.WriteField("prompt", req.Prompt)
	}
	if req.ResponseFormat != "" {
		writer.WriteField("response_format", req.ResponseFormat)
	}
	if req.Temperature != nil {
		writer.WriteField("temperature", fmt.Sprintf("%g", *req.Temperature))
	}

	if err := writer.Close(); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("closing multipart writer: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/audio/translations", &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating translation request: %v", err))
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("translation request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("translation request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading translation response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.TranslationResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing translation response: %v", err))
	}

	return &result, nil
}

// CreateEmbedding sends an embedding request.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling embedding request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating embedding request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("embedding request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("embedding request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading embedding response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result models.EmbeddingResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing embedding response: %v", err))
	}

	return &result, nil
}

// SubmitBatch submits a batch of requests to OpenAI's Batch API.
func (p *Provider) SubmitBatch(ctx context.Context, requests []models.BatchRequest) (string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return "", models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	// Build JSONL content.
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	for i := range requests {
		requests[i].Body.Model = resolveModelName(requests[i].Body.Model)
		line := struct {
			CustomID string                       `json:"custom_id"`
			Method   string                       `json:"method"`
			URL      string                       `json:"url"`
			Body     models.ChatCompletionRequest `json:"body"`
		}{
			CustomID: requests[i].CustomID,
			Method:   "POST",
			URL:      "/v1/chat/completions",
			Body:     requests[i].Body,
		}
		if err := enc.Encode(line); err != nil {
			return "", models.ErrInternal(fmt.Sprintf("encoding batch request %d: %v", i, err))
		}
	}

	// Upload JSONL file.
	var formBuf bytes.Buffer
	writer := multipart.NewWriter(&formBuf)
	writer.WriteField("purpose", "batch")
	filePart, err := writer.CreateFormFile("file", "batch_input.jsonl")
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("creating batch file part: %v", err))
	}
	filePart.Write(buf.Bytes())
	writer.Close()

	uploadReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/files", &formBuf)
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("creating file upload request: %v", err))
	}
	uploadReq.Header.Set("Content-Type", writer.FormDataContentType())
	if p.apiKey != "" {
		uploadReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	uploadResp, err := p.httpClient.Do(uploadReq)
	if err != nil {
		if ctx.Err() != nil {
			return "", models.ErrGatewayTimeout("batch file upload timed out")
		}
		return "", models.ErrUpstreamProvider(0, fmt.Sprintf("batch file upload failed: %v", err))
	}
	defer uploadResp.Body.Close()

	uploadBody, err := io.ReadAll(io.LimitReader(uploadResp.Body, 10*1024*1024))
	if err != nil {
		return "", models.ErrUpstreamProvider(uploadResp.StatusCode, fmt.Sprintf("reading file upload response: %v", err))
	}
	if uploadResp.StatusCode != http.StatusOK {
		return "", parseProviderError(uploadResp.StatusCode, uploadBody)
	}

	var fileResp struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(uploadBody, &fileResp); err != nil {
		return "", models.ErrUpstreamProvider(uploadResp.StatusCode, fmt.Sprintf("parsing file upload response: %v", err))
	}

	// Create batch.
	batchReq := struct {
		InputFileID      string `json:"input_file_id"`
		Endpoint         string `json:"endpoint"`
		CompletionWindow string `json:"completion_window"`
	}{
		InputFileID:      fileResp.ID,
		Endpoint:         "/v1/chat/completions",
		CompletionWindow: "24h",
	}

	batchBody, err := json.Marshal(batchReq)
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("marshaling batch request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/batches", bytes.NewReader(batchBody))
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("creating batch request: %v", err))
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return "", models.ErrGatewayTimeout("batch creation timed out")
		}
		return "", models.ErrUpstreamProvider(0, fmt.Sprintf("batch creation failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading batch response: %v", err))
	}
	if resp.StatusCode != http.StatusOK {
		return "", parseProviderError(resp.StatusCode, respBody)
	}

	var batchResp struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(respBody, &batchResp); err != nil {
		return "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing batch response: %v", err))
	}

	return batchResp.ID, nil
}

// GetBatch retrieves the status of an OpenAI batch.
func (p *Provider) GetBatch(ctx context.Context, batchID string) (*models.ProviderBatchStatus, error) {
	httpReq, err := http.NewRequestWithContext(ctx, "GET", p.baseURL+"/v1/batches/"+batchID, nil)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating get batch request: %v", err))
	}
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("get batch timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("get batch failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading batch status: %v", err))
	}
	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var oaiBatch struct {
		ID              string `json:"id"`
		Status          string `json:"status"`
		OutputFileID    string `json:"output_file_id"`
		ErrorFileID     string `json:"error_file_id"`
		CreatedAt       int64  `json:"created_at"`
		CompletedAt     *int64 `json:"completed_at"`
		RequestCounts   struct {
			Total     int `json:"total"`
			Completed int `json:"completed"`
			Failed    int `json:"failed"`
		} `json:"request_counts"`
	}
	if err := json.Unmarshal(respBody, &oaiBatch); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing batch status: %v", err))
	}

	return &models.ProviderBatchStatus{
		ID:           oaiBatch.ID,
		Status:       mapOpenAIBatchStatus(oaiBatch.Status),
		Total:        oaiBatch.RequestCounts.Total,
		Completed:    oaiBatch.RequestCounts.Completed,
		Failed:       oaiBatch.RequestCounts.Failed,
		OutputFileID: oaiBatch.OutputFileID,
		ErrorFileID:  oaiBatch.ErrorFileID,
		CreatedAt:    oaiBatch.CreatedAt,
		CompletedAt:  oaiBatch.CompletedAt,
	}, nil
}

// CancelBatch cancels an OpenAI batch.
func (p *Provider) CancelBatch(ctx context.Context, batchID string) error {
	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/batches/"+batchID+"/cancel", nil)
	if err != nil {
		return models.ErrInternal(fmt.Sprintf("creating cancel batch request: %v", err))
	}
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return models.ErrGatewayTimeout("cancel batch timed out")
		}
		return models.ErrUpstreamProvider(0, fmt.Sprintf("cancel batch failed: %v", err))
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		return parseProviderError(resp.StatusCode, respBody)
	}

	return nil
}

func mapOpenAIBatchStatus(status string) string {
	switch status {
	case "validating", "in_progress":
		return models.BatchStatusProcessing
	case "completed":
		return models.BatchStatusCompleted
	case "failed":
		return models.BatchStatusFailed
	case "cancelled", "cancelling":
		return models.BatchStatusCancelled
	case "expired":
		return models.BatchStatusExpired
	default:
		return models.BatchStatusQueued
	}
}

// ProxyAssistantsRequest forwards a non-streaming Assistants API request to OpenAI.
func (p *Provider) ProxyAssistantsRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) ([]byte, int, http.Header, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	upstreamURL := p.baseURL + path
	if len(queryParams) > 0 {
		upstreamURL += "?" + queryParams.Encode()
	}

	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}

	httpReq, err := http.NewRequestWithContext(ctx, method, upstreamURL, bodyReader)
	if err != nil {
		return nil, 0, nil, models.ErrInternal(fmt.Sprintf("creating assistants request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("OpenAI-Beta", "assistants=v2")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, nil, models.ErrGatewayTimeout("assistants request timed out")
		}
		return nil, 0, nil, models.ErrUpstreamProvider(0, fmt.Sprintf("assistants request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading assistants response: %v", err))
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.StatusCode, nil, parseProviderError(resp.StatusCode, respBody)
	}

	// Forward selected response headers.
	fwdHeaders := make(http.Header)
	for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
		if v := resp.Header.Get(key); v != "" {
			fwdHeaders.Set(key, v)
		}
	}

	return respBody, resp.StatusCode, fwdHeaders, nil
}

// StreamAssistantsRequest forwards a streaming Assistants API request to OpenAI.
func (p *Provider) StreamAssistantsRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) (io.ReadCloser, int, http.Header, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	upstreamURL := p.baseURL + path
	if len(queryParams) > 0 {
		upstreamURL += "?" + queryParams.Encode()
	}

	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}

	httpReq, err := http.NewRequestWithContext(ctx, method, upstreamURL, bodyReader)
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, nil, models.ErrInternal(fmt.Sprintf("creating streaming assistants request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "text/event-stream")
	httpReq.Header.Set("OpenAI-Beta", "assistants=v2")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, 0, nil, models.ErrGatewayTimeout("streaming assistants request timed out")
		}
		return nil, 0, nil, models.ErrUpstreamProvider(0, fmt.Sprintf("streaming assistants request failed: %v", err))
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if readErr != nil {
			return nil, resp.StatusCode, nil, models.ErrUpstreamProvider(resp.StatusCode,
				fmt.Sprintf("assistants error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
		}
		return nil, resp.StatusCode, nil, parseProviderError(resp.StatusCode, respBody)
	}

	// Forward selected response headers.
	fwdHeaders := make(http.Header)
	for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
		if v := resp.Header.Get(key); v != "" {
			fwdHeaders.Set(key, v)
		}
	}

	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, resp.StatusCode, fwdHeaders, nil
}

// ProxyVectorStoresRequest forwards a Vector Stores API request (no OpenAI-Beta header).
func (p *Provider) ProxyVectorStoresRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) ([]byte, int, http.Header, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	upstreamURL := p.baseURL + path
	if len(queryParams) > 0 {
		upstreamURL += "?" + queryParams.Encode()
	}

	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}

	httpReq, err := http.NewRequestWithContext(ctx, method, upstreamURL, bodyReader)
	if err != nil {
		return nil, 0, nil, models.ErrInternal(fmt.Sprintf("creating vector stores request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, nil, models.ErrGatewayTimeout("vector stores request timed out")
		}
		return nil, 0, nil, models.ErrUpstreamProvider(0, fmt.Sprintf("vector stores request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading vector stores response: %v", err))
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.StatusCode, nil, parseProviderError(resp.StatusCode, respBody)
	}

	fwdHeaders := make(http.Header)
	for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
		if v := resp.Header.Get(key); v != "" {
			fwdHeaders.Set(key, v)
		}
	}

	return respBody, resp.StatusCode, fwdHeaders, nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}

func parseProviderError(status int, body []byte) *models.APIError {
	// Try to parse as OpenAI error format.
	var errResp models.ErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		return &models.APIError{
			Status:  mapProviderStatus(status),
			Type:    errResp.Error.Type,
			Code:    errResp.Error.Code,
			Message: errResp.Error.Message,
		}
	}

	// Fallback: use raw body as message.
	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("provider error (HTTP %d): %s", status, msg))
}

// resolveModelName returns the model ID as-is. Historically this stripped
// a leading "{prefix}/" routing token, but that's wrong for OpenAI-compatible
// providers like Groq, Together, and Fireworks whose upstream model IDs
// legitimately contain slashes (e.g. "groq/compound-mini",
// "meta-llama/llama-4-scout-17b-16e-instruct", "openai/gpt-oss-120b"). The
// gateway's routing layer resolves the target provider before reaching here,
// so the caller's req.Model is already the upstream-native identifier.
func resolveModelName(model string) string {
	return model
}

func mapProviderStatus(status int) int {
	switch {
	case status == 429:
		return http.StatusTooManyRequests
	case status >= 500:
		return http.StatusBadGateway
	case status >= 400:
		return status // pass through 4xx
	default:
		return http.StatusBadGateway
	}
}
