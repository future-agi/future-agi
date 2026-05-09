package gemini

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/gauth"
)

type Provider struct {
	id            string
	baseURL       string
	apiKey        string
	vertexAI      bool
	tokenProvider *gauth.TokenProvider
	httpClient    *http.Client
	semaphore     chan struct{}
	headers       map[string]string
}

func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	timeout := cfg.DefaultTimeout
	if timeout == 0 {
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

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
	}

	baseURL := strings.TrimRight(cfg.BaseURL, "/")

	vertexAI := isVertexAI(baseURL)
	if vertexAI {
		project := cfg.Headers["x-gcp-project"]
		location := cfg.Headers["x-gcp-location"]
		if project != "" && location != "" && !strings.Contains(baseURL, "/projects/") {
			baseURL = fmt.Sprintf("%s/v1beta1/projects/%s/locations/%s", baseURL, project, location)
		}
	}

	p := &Provider{
		id:       id,
		baseURL:  baseURL,
		apiKey:   cfg.APIKey,
		vertexAI: vertexAI,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   timeout,
		},
		semaphore: make(chan struct{}, maxConcurrent),
		headers:   cfg.Headers,
	}

	if vertexAI && cfg.CredentialsFile != "" {
		tp, err := gauth.NewTokenProvider(cfg.CredentialsFile, gauth.ScopeCloudPlatform)
		if err != nil {
			return nil, fmt.Errorf("gemini: vertex ai credentials: %w", err)
		}
		p.tokenProvider = tp
	}

	return p, nil
}

func (p *Provider) ID() string { return p.id }

func (p *Provider) bearerToken() string {
	if p.tokenProvider != nil {
		return p.tokenProvider.Token()
	}
	return p.apiKey
}

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

// ChatCompletion sends a non-streaming request to Gemini.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	gr, model := translateRequest(req)

	body, err := json.Marshal(gr)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: marshaling request: %v", err))
	}

	url := buildURL(p.baseURL, model, false)
	if !p.vertexAI && p.apiKey != "" {
		url += "?key=" + p.apiKey
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("gemini: creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.vertexAI {
		if tok := p.bearerToken(); tok != "" {
			httpReq.Header.Set("Authorization", "Bearer "+tok)
		}
	}
	for k, v := range p.headers {
		httpReq.Header.Set(k, v)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("gemini: request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseGeminiError(resp.StatusCode, respBody)
	}

	var gr2 geminiResponse
	if err := json.Unmarshal(respBody, &gr2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing response: %v", err))
	}

	return translateResponse(&gr2, model), nil
}

// StreamChatCompletion sends a streaming request to Gemini.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- models.ErrGatewayTimeout("gemini: concurrency limit reached")
			return
		}
		defer p.releaseSemaphore()

		gr, model := translateRequest(req)

		body, err := json.Marshal(gr)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("gemini: marshaling request: %v", err))
			return
		}

		url := buildURL(p.baseURL, model, true)
		if !p.vertexAI && p.apiKey != "" {
			// streamGenerateContent already has ?alt=sse, append key.
			url += "&key=" + p.apiKey
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("gemini: creating request: %v", err))
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		if p.vertexAI {
			if tok := p.bearerToken(); tok != "" {
				httpReq.Header.Set("Authorization", "Bearer "+tok)
			}
		}
		for k, v := range p.headers {
			httpReq.Header.Set(k, v)
		}

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("gemini: request timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: request failed: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("gemini: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
				return
			}
			errs <- parseGeminiError(resp.StatusCode, respBody)
			return
		}

		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

		state := newStreamState(model)

		for scanner.Scan() {
			line := scanner.Text()

			if line == "" || !strings.HasPrefix(line, "data: ") {
				continue
			}

			data := strings.TrimPrefix(line, "data: ")

			chunk, err := state.parseStreamData(data)
			if err != nil {
				slog.Warn("gemini: error parsing stream data",
					"error", err,
					"provider", p.id,
				)
				continue
			}

			if chunk != nil && len(chunk.Choices) > 0 {
				select {
				case chunks <- *chunk:
				case <-ctx.Done():
					return
				}
			}
		}

		if err := scanner.Err(); err != nil {
			if ctx.Err() == nil {
				errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: stream read error: %v", err))
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models. Gemini has a list models endpoint but
// we return empty for now — models are configured via provider config.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// --- GenAINativeProvider implementation (native Google GenAI pass-through) ---

// GenerateContent sends a raw Google GenAI generateContent request.
func (p *Provider) GenerateContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	return p.doGenAIRequest(ctx, model, "generateContent", reqBody, headers)
}

// GenAICountTokens sends a raw Google GenAI countTokens request.
func (p *Provider) GenAICountTokens(ctx context.Context, model string, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	return p.doGenAIRequest(ctx, model, "countTokens", reqBody, headers)
}

// EmbedContent sends a raw Google GenAI embedContent request.
func (p *Provider) EmbedContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	return p.doGenAIRequest(ctx, model, "embedContent", reqBody, headers)
}

// StreamGenerateContent sends a streaming Google GenAI request.
func (p *Provider) StreamGenerateContent(ctx context.Context, model string, reqBody []byte, headers map[string]string) (io.ReadCloser, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	// Semaphore released when caller closes the stream.

	url := p.buildNativeURL(model, "streamGenerateContent") + "?alt=sse"

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(reqBody))
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, models.ErrInternal(fmt.Sprintf("gemini: creating native stream request: %v", err))
	}

	p.setNativeGenAIHeaders(httpReq, headers)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("gemini: streaming request timed out")
		}
		return nil, 502, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: streaming request failed: %v", err))
	}

	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, string(respBody))
	}

	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, resp.StatusCode, nil
}

// doGenAIRequest is a shared helper for non-streaming native GenAI requests.
func (p *Provider) doGenAIRequest(ctx context.Context, model string, action string, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	url := p.buildNativeURL(model, action)

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("gemini: creating native request: %v", err))
	}

	p.setNativeGenAIHeaders(httpReq, headers)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("gemini: native request timed out")
		}
		return nil, 502, models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: native request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading native response: %v", err))
	}

	return respBody, resp.StatusCode, nil
}

// buildNativeURL constructs the upstream URL for native GenAI requests.
func (p *Provider) buildNativeURL(model string, action string) string {
	if p.vertexAI {
		if strings.Contains(p.baseURL, "/projects/") {
			return fmt.Sprintf("%s/publishers/google/models/%s:%s", p.baseURL, model, action)
		}
		return fmt.Sprintf("%s/v1beta1/models/%s:%s", p.baseURL, model, action)
	}
	return fmt.Sprintf("%s/v1beta/models/%s:%s", p.baseURL, model, action)
}

// setNativeGenAIHeaders sets headers for native GenAI pass-through requests.
func (p *Provider) setNativeGenAIHeaders(req *http.Request, headers map[string]string) {
	req.Header.Set("Content-Type", "application/json")

	// Set auth based on provider type.
	if p.vertexAI {
		if tok := p.bearerToken(); tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
	} else if !p.vertexAI && p.apiKey != "" {
		req.Header.Set("x-goog-api-key", p.apiKey)
	}

	// Forward caller-provided headers (e.g., x-goog-api-client).
	for k, v := range headers {
		req.Header.Set(k, v)
	}

	// Apply provider config headers (lower priority — don't overwrite).
	for k, v := range p.headers {
		if req.Header.Get(k) == "" {
			req.Header.Set(k, v)
		}
	}
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

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}
