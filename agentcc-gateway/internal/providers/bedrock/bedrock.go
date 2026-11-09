package bedrock

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

const bedrockService = "bedrock"

// Provider implements the Provider interface for AWS Bedrock.
type Provider struct {
	id          string
	baseURL     string
	region      string
	credentials *Credentials
	httpClient  *http.Client
	slots       *admission.Limiter
	headers     map[string]string
}

// New creates a new Bedrock provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	errorCredentials := common.HeldErrorCredentials(cfg.Headers, cfg.APIKey, cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey, cfg.AWSSessionToken)

	creds, err := loadCredentials(cfg)
	if err != nil {
		return nil, common.ExactError(fmt.Errorf("bedrock: loading credentials: %w", err), errorCredentials...)
	}

	timeout := cfg.DefaultTimeout
	if timeout == 0 {
		timeout = 120 * time.Second
	}

	// max_concurrent, conn_pool_size and queue_timeout come resolved from the
	// registry (local or remote defaults); admission and these defaults cover
	// an instance built from a config that left them unset.
	poolSize := cfg.ConnPoolSize
	if poolSize <= 0 {
		poolSize = admission.DefaultConnPoolSize
	}

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
		DialContext:         cfg.DialContext,
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
		slots:   admission.New(id, cfg.MaxConcurrent, cfg.QueueTimeout),
		headers: cfg.Headers,
	}, nil
}

func (p *Provider) ID() string { return p.id }

// UpstreamModelName is the id every call sends Bedrock for model
// (providers.UpstreamModelNamer).
func (p *Provider) UpstreamModelName(model string) string { return resolveModelName(model) }

// ClientTimeout reports the timeout on this provider's HTTP client. It bounds
// the whole exchange, a streamed body included, so it is a hard ceiling on any
// request this provider serves.
func (p *Provider) ClientTimeout() time.Duration { return p.httpClient.Timeout }

// acquireSemaphore takes one of the entry's upstream slots. Its error, a
// provider_busy 503 once the queue wait ends or a gateway timeout when ctx
// ends first, is the call's answer: nothing was sent upstream.
func (p *Provider) acquireSemaphore(ctx context.Context) error {
	return p.slots.Acquire(ctx)
}

func (p *Provider) releaseSemaphore() {
	p.slots.Release()
}

// Admission is the instance's upstream slots (providers.SlotHolder).
func (p *Provider) Admission() *admission.Limiter { return p.slots }

// ChatCompletion sends a non-streaming request to Bedrock.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	errorCredentials := common.HeldErrorCredentials(p.headers, p.credentials.AccessKeyID, p.credentials.SecretAccessKey, p.credentials.SessionToken)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	modelID := resolveModelName(req.Model)
	if isInferenceProfile(modelID) {
		return p.chatCompletionConverse(ctx, req)
	}

	br, _ := translateRequest(req)

	body, err := json.Marshal(br)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: marshaling request: %v", err)), errorCredentials...)
	}

	reqURL := fmt.Sprintf("%s/model/%s/invoke", p.baseURL, url.PathEscape(modelID))

	httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: creating request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")

	if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: signing request: %v", err)), errorCredentials...)
	}

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("bedrock: request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock: request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "bedrock: reading response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseBedrockError(resp.StatusCode, respBody, errorCredentials...)
	}

	var br2 bedrockResponse
	if err := json.Unmarshal(respBody, &br2); err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("bedrock: parsing response: %v", err)), errorCredentials...)
	}

	return translateResponse(&br2), nil
}

// StreamChatCompletion sends a streaming request to Bedrock.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	errorCredentials := common.HeldErrorCredentials(p.headers, p.credentials.AccessKeyID, p.credentials.SecretAccessKey, p.credentials.SessionToken)

	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- err
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
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: marshaling request: %v", err)), errorCredentials...)
			return
		}

		reqURL := fmt.Sprintf("%s/model/%s/invoke-with-response-stream", p.baseURL, url.PathEscape(modelID))

		httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
		if err != nil {
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: creating request: %v", err)), errorCredentials...)
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "application/vnd.amazon.eventstream")

		if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("bedrock: signing request: %v", err)), errorCredentials...)
			return
		}

		errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if common.TimedOut(ctx, err) {
				errs <- common.ExactAPIError(models.ErrGatewayTimeout("bedrock: request timed out"), errorCredentials...)
				return
			}
			errs <- common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock: request failed: %v", err)), errorCredentials...)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("bedrock: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr)), errorCredentials...)
				return
			}
			errs <- parseBedrockError(resp.StatusCode, respBody, errorCredentials...)
			return
		}

		state := newBedrockStreamState(modelID)

		for {
			select {
			case <-ctx.Done():
				return
			default:
			}

			msg, err := readEventStreamMessage(resp.Body, errorCredentials...)
			if err != nil {
				if ctx.Err() == nil {
					errs <- common.ReadError(err, 0, "bedrock: stream read", errorCredentials...)
				}
				return
			}
			if msg == nil {
				return // EOF.
			}

			chunk, done, err := state.parseStreamPayload(msg, errorCredentials...)
			if err != nil {
				slog.Warn("bedrock: error parsing stream event",
					"error", common.ExactError(err, errorCredentials...),
					"provider", p.id,
				)
				// Bedrock exceptions are fatal.
				if msg.Headers[":message-type"] == "exception" {
					errs <- common.ExactAPIError(models.ErrUpstreamProvider(0, privacy.ScrubExactCredential(err.Error(), errorCredentials...)), errorCredentials...)
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
	p.slots.Close()
	p.httpClient.CloseIdleConnections()
	return nil
}
