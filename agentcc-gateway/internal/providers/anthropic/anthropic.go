package anthropic

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
)

const defaultAnthropicVersion = "2023-06-01"

// Provider implements the Provider interface for the Anthropic Messages API.
type Provider struct {
	id         string
	baseURL    string
	apiKey     string
	httpClient *http.Client
	semaphore  chan struct{}
	headers    map[string]string
}

// New creates a new Anthropic provider.
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

	return &Provider{
		id:      id,
		baseURL: strings.TrimRight(cfg.BaseURL, "/"),
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

// ChatCompletion sends a non-streaming chat completion request to Anthropic.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("anthropic: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	ar, err := translateRequest(req)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("anthropic: translating request: %v", err))
	}
	ar.Stream = false

	body, err := json.Marshal(ar)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("anthropic: marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("anthropic: creating request: %v", err))
	}

	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("anthropic: request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: reading response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseAnthropicError(resp.StatusCode, respBody)
	}

	var ar2 anthropicResponse
	if err := json.Unmarshal(respBody, &ar2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: parsing response: %v", err))
	}

	return translateResponse(&ar2), nil
}

// StreamChatCompletion sends a streaming chat completion request to Anthropic.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- models.ErrGatewayTimeout("anthropic: concurrency limit reached")
			return
		}
		defer p.releaseSemaphore()

		ar, err := translateRequest(req)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("anthropic: translating request: %v", err))
			return
		}
		ar.Stream = true

		body, err := json.Marshal(ar)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("anthropic: marshaling request: %v", err))
			return
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages", bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("anthropic: creating request: %v", err))
			return
		}

		p.setHeaders(httpReq)
		httpReq.Header.Set("Accept", "text/event-stream")

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("anthropic: request timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: request failed: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("anthropic: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
				return
			}
			errs <- parseAnthropicError(resp.StatusCode, respBody)
			return
		}

		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

		state := newStreamState()
		var currentEvent string

		for scanner.Scan() {
			line := scanner.Text()

			if strings.HasPrefix(line, "event: ") {
				currentEvent = strings.TrimPrefix(line, "event: ")
				continue
			}

			if !strings.HasPrefix(line, "data: ") {
				continue
			}

			data := strings.TrimPrefix(line, "data: ")
			_ = currentEvent // Event type is also embedded in the JSON data.

			chunk, done, err := state.parseSSELine(currentEvent, data)
			if err != nil {
				slog.Warn("anthropic: error parsing stream event",
					"error", err,
					"provider", p.id,
				)
				continue
			}

			if chunk != nil {
				select {
				case chunks <- *chunk:
				case <-ctx.Done():
					return
				}
			}

			if done {
				return
			}
		}

		if err := scanner.Err(); err != nil {
			if ctx.Err() == nil {
				errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: stream read error: %v", err))
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models. Anthropic doesn't have a list models endpoint,
// so we return an empty list. Models are configured via the provider config.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// CreateAnthropicMessage sends a raw Anthropic Messages API request (native pass-through).
func (p *Provider) CreateAnthropicMessage(ctx context.Context, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages", bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating anthropic native request: %v", err))
	}

	p.setNativeHeaders(httpReq, headers)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("anthropic request timed out")
		}
		return nil, 502, models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading anthropic response: %v", err))
	}

	return respBody, resp.StatusCode, nil
}

// CountAnthropicTokens sends a raw Anthropic count_tokens request.
func (p *Provider) CountAnthropicTokens(ctx context.Context, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages/count_tokens", bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating anthropic count_tokens request: %v", err))
	}

	p.setNativeHeaders(httpReq, headers)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("anthropic count_tokens request timed out")
		}
		return nil, 502, models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic count_tokens request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading anthropic count_tokens response: %v", err))
	}

	return respBody, resp.StatusCode, nil
}

// StreamAnthropicMessage sends a streaming native Anthropic Messages API request.
func (p *Provider) StreamAnthropicMessage(ctx context.Context, reqBody []byte, headers map[string]string) (io.ReadCloser, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// Semaphore released when caller closes the stream.

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages", bytes.NewReader(reqBody))
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating streaming anthropic request: %v", err))
	}

	p.setNativeHeaders(httpReq, headers)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, 0, models.ErrGatewayTimeout("streaming anthropic request timed out")
		}
		return nil, 502, models.ErrUpstreamProvider(0, fmt.Sprintf("streaming anthropic request failed: %v", err))
	}

	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		return nil, resp.StatusCode, models.ErrUpstreamProvider(resp.StatusCode, string(respBody))
	}

	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, resp.StatusCode, nil
}

// setNativeHeaders sets headers for native Anthropic API requests.
func (p *Provider) setNativeHeaders(req *http.Request, headers map[string]string) {
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", p.apiKey)

	// Set default version if not overridden.
	version := defaultAnthropicVersion
	if v, ok := headers["anthropic-version"]; ok && v != "" {
		version = v
	}
	req.Header.Set("anthropic-version", version)

	// Forward other Anthropic-specific headers.
	for k, v := range headers {
		if k != "anthropic-version" { // already set above
			req.Header.Set(k, v)
		}
	}

	// Apply provider config headers.
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

func (p *Provider) setHeaders(req *http.Request) {
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", p.apiKey)

	// Set anthropic-version from config headers or use default.
	version := defaultAnthropicVersion
	if v, ok := p.headers["anthropic-version"]; ok {
		version = v
	}
	req.Header.Set("anthropic-version", version)

	// Apply any additional headers from config.
	for k, v := range p.headers {
		if k == "anthropic-version" {
			continue // Already set above.
		}
		req.Header.Set(k, v)
	}
}
