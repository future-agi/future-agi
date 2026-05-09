package bedrock

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const bedrockService = "bedrock"

// Provider implements the Provider interface for AWS Bedrock.
type Provider struct {
	id          string
	baseURL     string
	region      string
	credentials *Credentials
	httpClient  *http.Client
	semaphore   chan struct{}
	headers     map[string]string
}

// New creates a new Bedrock provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	creds, err := loadCredentials(cfg)
	if err != nil {
		return nil, fmt.Errorf("bedrock: loading credentials: %w", err)
	}

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

	region := extractRegion(cfg.BaseURL, cfg.Headers, cfg.AWSRegion)

	return &Provider{
		id:          id,
		baseURL:     strings.TrimRight(cfg.BaseURL, "/"),
		region:      region,
		credentials: creds,
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

// ChatCompletion sends a non-streaming request to Bedrock.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("bedrock: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	modelID := resolveModelName(req.Model)
	if isInferenceProfile(modelID) {
		return p.chatCompletionConverse(ctx, req)
	}

	br, _ := translateRequest(req)

	body, err := json.Marshal(br)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock: marshaling request: %v", err))
	}

	reqURL := fmt.Sprintf("%s/model/%s/invoke", p.baseURL, url.PathEscape(modelID))

	httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock: creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")

	if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock: signing request: %v", err))
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("bedrock: request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock: request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("bedrock: reading response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseBedrockError(resp.StatusCode, respBody)
	}

	var br2 bedrockResponse
	if err := json.Unmarshal(respBody, &br2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("bedrock: parsing response: %v", err))
	}

	return translateResponse(&br2), nil
}

// StreamChatCompletion sends a streaming request to Bedrock.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- models.ErrGatewayTimeout("bedrock: concurrency limit reached")
			return
		}
		defer p.releaseSemaphore()

		modelID := resolveModelName(req.Model)
		if isInferenceProfile(modelID) {
			ch, er := p.streamChatCompletionConverse(ctx, req)
			for {
				select {
				case chunk, ok := <-ch:
					if !ok {
						ch = nil
					} else {
						chunks <- chunk
					}
				case err, ok := <-er:
					if !ok {
						er = nil
					} else {
						errs <- err
					}
				}
				if ch == nil && er == nil {
					return
				}
			}
		}

		br, _ := translateRequest(req)

		body, err := json.Marshal(br)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock: marshaling request: %v", err))
			return
		}

		reqURL := fmt.Sprintf("%s/model/%s/invoke-with-response-stream", p.baseURL, url.PathEscape(modelID))

		httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock: creating request: %v", err))
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "application/vnd.amazon.eventstream")

		if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock: signing request: %v", err))
			return
		}

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("bedrock: request timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock: request failed: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("bedrock: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr))
				return
			}
			errs <- parseBedrockError(resp.StatusCode, respBody)
			return
		}

		state := newBedrockStreamState(modelID)

		for {
			select {
			case <-ctx.Done():
				return
			default:
			}

			msg, err := readEventStreamMessage(resp.Body)
			if err != nil {
				if ctx.Err() == nil {
					errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock: stream read error: %v", err))
				}
				return
			}
			if msg == nil {
				return // EOF.
			}

			chunk, done, err := state.parseStreamPayload(msg)
			if err != nil {
				slog.Warn("bedrock: error parsing stream event",
					"error", err,
					"provider", p.id,
				)
				// Bedrock exceptions are fatal.
				if msg.Headers[":message-type"] == "exception" {
					errs <- models.ErrUpstreamProvider(0, err.Error())
					return
				}
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
	}()

	return chunks, errs
}

// ListModels returns available models. Bedrock requires the ListFoundationModels API
// but we return empty for now — models are configured via provider config.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}
