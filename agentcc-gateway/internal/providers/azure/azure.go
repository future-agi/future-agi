package azure

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
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider implements the Provider interface for Azure OpenAI.
// Azure uses a different URL format: /openai/deployments/{deployment}/chat/completions?api-version={version}
type Provider struct {
	id         string
	baseURL    string // https://{resource}.openai.azure.com
	apiKey     string
	apiVersion string
	authType   string // "api-key" (default) or "bearer" (Azure AD)
	httpClient *http.Client
	semaphore  chan struct{}
}

// New creates a new Azure OpenAI provider.
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

	apiVersion := cfg.Headers["api-version"]
	if apiVersion == "" {
		apiVersion = "2024-10-21"
	}

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}
	if cfg.SkipTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec
		slog.Warn("TLS verification disabled for provider", "provider", id)
	}

	authType := cfg.Headers["auth-type"]
	if authType == "" {
		authType = "api-key"
	}

	return &Provider{
		id:         id,
		baseURL:    strings.TrimRight(cfg.BaseURL, "/"),
		apiKey:     cfg.APIKey,
		apiVersion: apiVersion,
		authType:   authType,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   timeout,
		},
		semaphore: make(chan struct{}, maxConcurrent),
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

// deploymentURL builds the Azure-specific URL for a deployment.
func (p *Provider) deploymentURL(deployment, endpoint string) string {
	return fmt.Sprintf("%s/openai/deployments/%s/%s?api-version=%s",
		p.baseURL, deployment, endpoint, p.apiVersion)
}

// setAuth sets Azure authentication headers.
// Supports both api-key (default) and bearer token (Azure AD) auth.
func (p *Provider) setAuth(req *http.Request) {
	if p.apiKey == "" {
		return
	}
	if p.authType == "bearer" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	} else {
		req.Header.Set("api-key", p.apiKey)
	}
}

// ChatCompletion sends a non-streaming chat completion request.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	reqCopy := *req
	reqCopy.Stream = false
	// Azure ignores the model field in the body — routing is via URL deployment name.

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
	}

	url := p.deploymentURL(deployment, "chat/completions")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

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

		deployment := resolveDeployment(req.Model)
		reqCopy := *req
		reqCopy.Stream = true

		body, err := json.Marshal(&reqCopy)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
			return
		}

		url := p.deploymentURL(deployment, "chat/completions")
		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("creating request: %v", err))
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		p.setAuth(httpReq)

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
					"error", err, "data", data, "provider", p.id)
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

// ListModels returns available models from Azure OpenAI.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	url := fmt.Sprintf("%s/openai/models?api-version=%s", p.baseURL, p.apiVersion)
	httpReq, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}
	p.setAuth(httpReq)

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

// CreateEmbedding sends an embedding request to Azure OpenAI.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling embedding request: %v", err))
	}

	url := p.deploymentURL(deployment, "embeddings")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating embedding request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

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

// CreateImage sends an image generation request to Azure OpenAI (DALL-E).
func (p *Provider) CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling image request: %v", err))
	}

	url := p.deploymentURL(deployment, "images/generations")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating image request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

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

// CreateSpeech sends a text-to-speech request to Azure OpenAI and returns an audio stream.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("marshaling speech request: %v", err))
	}

	url := p.deploymentURL(deployment, "audio/speech")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

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

// CreateTranscription sends an audio transcription request to Azure OpenAI (Whisper).
func (p *Provider) CreateTranscription(ctx context.Context, req *models.TranscriptionRequest) (*models.TranscriptionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

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

	// Add model field (Azure may ignore this, but include for compatibility).
	writer.WriteField("model", deployment)

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

	url := p.deploymentURL(deployment, "audio/transcriptions")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating transcription request: %v", err))
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	p.setAuth(httpReq)

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

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}

func parseProviderError(status int, body []byte) *models.APIError {
	var errResp models.ErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		return &models.APIError{
			Status:  mapProviderStatus(status),
			Type:    errResp.Error.Type,
			Code:    errResp.Error.Code,
			Message: errResp.Error.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("provider error (HTTP %d): %s", status, msg))
}

// resolveDeployment extracts the deployment name (strips provider prefix).
func resolveDeployment(model string) string {
	if idx := strings.Index(model, "/"); idx > 0 {
		return model[idx+1:]
	}
	return model
}

func mapProviderStatus(status int) int {
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
