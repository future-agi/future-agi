package cohere

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

// Provider implements the Provider interface for the Cohere v2 Chat API.
type Provider struct {
	id         string
	baseURL    string
	apiKey     string
	httpClient *http.Client
	semaphore  chan struct{}
	headers    map[string]string
}

// New creates a new Cohere provider.
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

// ChatCompletion sends a non-streaming request to Cohere.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("cohere: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	cr := translateRequest(req)
	cr.Stream = false

	model := resolveModelName(req.Model)

	body, err := json.Marshal(cr)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v2/chat", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: creating request: %v", err))
	}

	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("cohere: request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("cohere: request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: reading response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseCohereError(resp.StatusCode, respBody)
	}

	var cr2 cohereResponse
	if err := json.Unmarshal(respBody, &cr2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: parsing response: %v", err))
	}

	return translateResponse(&cr2, model), nil
}

// StreamChatCompletion sends a streaming request to Cohere.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- models.ErrGatewayTimeout("cohere: concurrency limit reached")
			return
		}
		defer p.releaseSemaphore()

		cr := translateRequest(req)
		cr.Stream = true
		model := resolveModelName(req.Model)

		body, err := json.Marshal(cr)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("cohere: marshaling request: %v", err))
			return
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v2/chat", bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("cohere: creating request: %v", err))
			return
		}

		p.setHeaders(httpReq)
		httpReq.Header.Set("Accept", "text/event-stream")

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("cohere: request timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("cohere: request failed: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("cohere: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
				return
			}
			errs <- parseCohereError(resp.StatusCode, respBody)
			return
		}

		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

		state := newStreamState(model)

		for scanner.Scan() {
			line := scanner.Text()

			if line == "" {
				continue
			}

			if !strings.HasPrefix(line, "data: ") {
				continue
			}

			data := strings.TrimPrefix(line, "data: ")

			chunk, done, err := state.parseStreamData(data)
			if err != nil {
				slog.Warn("cohere: error parsing stream event",
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
				errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("cohere: stream read error: %v", err))
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models. Cohere has a list models endpoint
// but we return empty for now — models are configured via provider config.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// CreateEmbedding sends an embedding request to Cohere.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("cohere: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	cr := translateEmbeddingRequest(req, p.headers)
	model := cr.Model

	body, err := json.Marshal(cr)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: marshaling embed request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v2/embed", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: creating embed request: %v", err))
	}

	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("cohere: embed request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("cohere: embed request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: reading embed response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseCohereError(resp.StatusCode, respBody)
	}

	var cr2 cohereEmbedResponse
	if err := json.Unmarshal(respBody, &cr2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: parsing embed response: %v", err))
	}

	return translateEmbeddingResponse(&cr2, model), nil
}

// Rerank sends a rerank request to Cohere.
func (p *Provider) Rerank(ctx context.Context, req *models.RerankRequest) (*models.RerankResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("cohere: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	cr := translateRerankRequest(req)

	body, err := json.Marshal(cr)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: marshaling rerank request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v2/rerank", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("cohere: creating rerank request: %v", err))
	}

	p.setHeaders(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("cohere: rerank request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("cohere: rerank request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: reading rerank response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseCohereError(resp.StatusCode, respBody)
	}

	var cr2 cohereRerankResponse
	if err := json.Unmarshal(respBody, &cr2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("cohere: parsing rerank response: %v", err))
	}

	return translateRerankResponse(&cr2), nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}

func (p *Provider) setHeaders(req *http.Request) {
	req.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	}
	for k, v := range p.headers {
		req.Header.Set(k, v)
	}
}
