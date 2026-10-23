// Package systemone serves structured decisions in the System One wire format:
// {model, state, questions: {<id>: {type, ...}}} in, {model, answers, usage}
// out. TypeSafe defined it; Perplexity's Decider and self-hosted endpoints
// speak it too. Each vendor is a Dialect row.
//
// The caller's body is forwarded byte for byte and the vendor's 200 body is
// returned unchanged, so a field either side adds passes through.
package systemone

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Provider posts System One requests to one vendor endpoint with one key.
type Provider struct {
	id         string
	dialect    Dialect
	endpoint   string
	apiKey     string
	models     []string
	timeout    time.Duration
	httpClient *http.Client
	semaphore  chan struct{}
}

// New builds a provider, picking its dialect from its ID or base_url.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	dialect, err := DialectFor(id, cfg.BaseURL)
	if err != nil {
		return nil, err
	}
	return NewForDialect(id, cfg, dialect)
}

// Check returns the error New would return, without building the provider.
func Check(id string, cfg config.ProviderConfig) error {
	dialect, err := DialectFor(id, cfg.BaseURL)
	if err != nil {
		return err
	}
	_, err = endpointFor(id, cfg, dialect)
	return err
}

// NewForDialect builds a provider for a dialect chosen by the caller.
func NewForDialect(id string, cfg config.ProviderConfig, dialect Dialect) (*Provider, error) {
	endpoint, err := endpointFor(id, cfg, dialect)
	if err != nil {
		return nil, err
	}
	timeout := cfg.DefaultTimeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	maxConcurrent := cfg.MaxConcurrent
	if maxConcurrent <= 0 {
		maxConcurrent = 100
	}
	poolSize := cfg.ConnPoolSize
	if poolSize <= 0 {
		poolSize = 100
	}
	return &Provider{
		id:       id,
		dialect:  dialect,
		endpoint: endpoint,
		apiKey:   cfg.APIKey,
		models:   append([]string(nil), cfg.Models...),
		timeout:  timeout,
		httpClient: &http.Client{
			Transport: &http.Transport{
				MaxIdleConns:        poolSize,
				MaxIdleConnsPerHost: poolSize,
				IdleConnTimeout:     90 * time.Second,
				ForceAttemptHTTP2:   true,
				DialContext:         cfg.DialContext,
				Proxy:               cfg.Proxy,
			},
			// A redirect must not carry the key or the request content anywhere
			// the provider was not configured to send them.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		semaphore: make(chan struct{}, maxConcurrent),
	}, nil
}

// endpointFor returns the URL a provider of dialect posts to: its base_url,
// or the dialect's default upstream when it states none.
func endpointFor(id string, cfg config.ProviderConfig, dialect Dialect) (string, error) {
	base := strings.TrimSpace(cfg.BaseURL)
	if base == "" {
		base = dialect.DefaultBase
	}
	if base == "" {
		return "", ErrBaseURLRequired
	}
	endpoint := config.JoinEndpoint(base, cfg.EffectiveAPIPathPrefix(), dialect.Leaf)
	if u, err := url.Parse(endpoint); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return "", fmt.Errorf("systemone: provider %q has an invalid base_url", id)
	}
	return endpoint, nil
}

func (p *Provider) ID() string { return p.id }

// Dialect returns the vendor dialect the provider speaks.
func (p *Provider) Dialect() Dialect { return p.dialect }

// Endpoint returns the URL decisions are posted to.
func (p *Provider) Endpoint() string { return p.endpoint }

func (p *Provider) notSupported() *models.APIError {
	return &models.APIError{
		Status:  http.StatusNotImplemented,
		Type:    models.ErrTypeServer,
		Code:    "not_supported",
		Message: fmt.Sprintf("%s serves structured decisions only; use /v1/systemone", p.dialect.Vendor),
	}
}

func (p *Provider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, p.notSupported()
}

func (p *Provider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error, 1)
	errs <- p.notSupported()
	close(chunks)
	close(errs)
	return chunks, errs
}

// ListModels echoes the configured models. Vendors are never asked: a
// listing call is not free on every one of them.
func (p *Provider) ListModels(context.Context) ([]models.ModelObject, error) {
	result := make([]models.ModelObject, 0, len(p.models))
	for _, id := range p.models {
		result = append(result, models.ModelObject{ID: id, Object: "model", OwnedBy: p.id})
	}
	return result, nil
}

func (p *Provider) Close() error { p.httpClient.CloseIdleConnections(); return nil }

// SystemOne posts req.Body to the vendor and returns its 200 body unchanged.
// The gateway does not retry; every failure maps onto the decision error
// table, and no error carries the key or the request's content.
func (p *Provider) SystemOne(ctx context.Context, req *models.DecisionRequest) (*models.DecisionResponse, error) {
	vendor := p.dialect.Vendor
	if p.apiKey == "" && p.dialect.KeyRequired {
		return nil, &models.APIError{
			Status:  http.StatusForbidden,
			Type:    models.ErrTypePermission,
			Code:    "provider_key_missing",
			Message: fmt.Sprintf("No %s key is saved for this provider.", vendor),
		}
	}

	// The timeout covers the wait for a concurrency slot too.
	ctx, cancel := context.WithTimeout(ctx, p.timeout)
	defer cancel()
	select {
	case p.semaphore <- struct{}{}:
		defer func() { <-p.semaphore }()
	case <-ctx.Done():
		return nil, models.ErrGatewayTimeout(vendor + " request timed out")
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, p.endpoint, bytes.NewReader(req.Body))
	if err != nil {
		return nil, models.ErrInternal("systemone: could not create request")
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set(p.dialect.AuthHeader, p.dialect.AuthScheme+p.apiKey)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		return nil, models.DecisionTransportError(ctx, err, vendor)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, models.MaxDecisionResponseBytes+1))
	if err != nil {
		return nil, models.DecisionTransportError(ctx, err, vendor)
	}

	if resp.StatusCode != http.StatusOK {
		message := req.UpstreamErrorMessage(resp.StatusCode, data, p.apiKey, vendor+" rejected the request.")
		return nil, models.DecisionUpstreamError(vendor, resp.StatusCode, resp.Header, message)
	}
	if len(data) > models.MaxDecisionResponseBytes {
		return nil, models.DecisionInvalidResponse(vendor)
	}
	parsed, ok := models.ParseDecisionResponse(data)
	if !ok {
		return nil, models.DecisionInvalidResponse(vendor)
	}
	return parsed, nil
}
