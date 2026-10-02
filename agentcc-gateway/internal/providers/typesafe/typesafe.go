// Package typesafe implements TypeSafe AI's structured System One endpoint.
package typesafe

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

type Provider struct {
	id         string
	baseURL    string
	apiKey     string
	models     []string
	httpClient *http.Client
	semaphore  chan struct{}
}

func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	if baseURL == "" {
		baseURL = "https://api.typesafe.ai"
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
		id: id, baseURL: baseURL, apiKey: cfg.APIKey, models: append([]string(nil), cfg.Models...),
		semaphore: make(chan struct{}, maxConcurrent),
		httpClient: &http.Client{
			Timeout:   timeout,
			Transport: &http.Transport{MaxIdleConns: poolSize, MaxIdleConnsPerHost: poolSize, IdleConnTimeout: 90 * time.Second, ForceAttemptHTTP2: true, DialContext: cfg.DialContext},
			// A redirect must not forward the provider key or request content.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func (p *Provider) ID() string { return p.id }

func notSupported() *models.APIError {
	return &models.APIError{Status: http.StatusNotImplemented, Type: models.ErrTypeServer, Code: "not_supported", Message: "TypeSafe does not support chat completions; use /v1/systemone"}
}

func (p *Provider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, notSupported()
}

func (p *Provider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error, 1)
	errs <- notSupported()
	close(chunks)
	close(errs)
	return chunks, errs
}

func (p *Provider) ListModels(context.Context) ([]models.ModelObject, error) {
	result := make([]models.ModelObject, 0, len(p.models))
	for _, id := range p.models {
		result = append(result, models.ModelObject{ID: id, Object: "model", OwnedBy: p.id})
	}
	return result, nil
}

func (p *Provider) Close() error { p.httpClient.CloseIdleConnections(); return nil }

func (p *Provider) SystemOne(ctx context.Context, req *models.SystemOneRequest) (*models.SystemOneResponse, error) {
	// The provider timeout includes time waiting for a concurrency slot.
	ctx, cancel := context.WithTimeout(ctx, p.httpClient.Timeout)
	defer cancel()
	select {
	case p.semaphore <- struct{}{}:
		defer func() { <-p.semaphore }()
	case <-ctx.Done():
		return nil, models.ErrGatewayTimeout("typesafe: request timed out")
	}
	body, err := json.Marshal(req)
	if err != nil {
		return nil, models.ErrInternal("typesafe: could not encode request")
	}
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, p.baseURL+"/v1/systemone", bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal("typesafe: could not create request")
	}
	httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	httpReq.Header.Set("Content-Type", "application/json")
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		return nil, transportError(ctx, err)
	}
	defer resp.Body.Close()
	const maxResponseBytes = 10 * 1024 * 1024
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return nil, transportError(ctx, err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, p.upstreamError(resp.StatusCode, data, req)
	}
	var parsed struct {
		Model   string                     `json:"model"`
		Answers map[string]json.RawMessage `json:"answers"`
		Usage   *models.SystemOneUsage     `json:"usage"`
	}
	if len(data) > maxResponseBytes || json.Unmarshal(data, &parsed) != nil || strings.TrimSpace(parsed.Model) == "" || len(parsed.Answers) == 0 || parsed.Usage == nil {
		apiErr := models.ErrInternal("typesafe: invalid upstream response")
		apiErr.Code = "upstream_invalid_response"
		return nil, apiErr
	}
	return &models.SystemOneResponse{Model: parsed.Model, Answers: parsed.Answers, Usage: *parsed.Usage}, nil
}

func transportError(ctx context.Context, err error) *models.APIError {
	var netErr net.Error
	if ctx.Err() != nil || errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()) {
		return models.ErrGatewayTimeout("typesafe: request timed out")
	}
	return &models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Code: "upstream_error", Message: "typesafe: upstream request failed"}
}

func (p *Provider) upstreamError(status int, body []byte, req *models.SystemOneRequest) *models.APIError {
	switch status {
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		return models.ErrBadRequest("upstream_rejected", p.safeMessage(body, req))
	case http.StatusUnauthorized, http.StatusForbidden:
		return &models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Code: "upstream_auth", Message: "typesafe: upstream authentication failed"}
	case http.StatusTooManyRequests:
		return models.ErrTooManyRequests("typesafe: upstream rate limit exceeded")
	default:
		return &models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Code: "upstream_error", Message: "typesafe: upstream request failed"}
	}
}

// safeMessage retains useful validation messages but discards any message that
// echoes credentials or request content, including JSON-escaped strings.
func (p *Provider) safeMessage(body []byte, req *models.SystemOneRequest) string {
	const generic = "typesafe: upstream rejected request"
	var envelope struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &envelope) != nil || strings.TrimSpace(envelope.Error.Message) == "" {
		return generic
	}
	message := envelope.Error.Message
	contains := func(value string) bool {
		if value == "" {
			return false
		}
		escaped, _ := json.Marshal(value)
		return strings.Contains(message, value) || strings.Contains(message, string(escaped[1:len(escaped)-1]))
	}
	if contains(p.apiKey) {
		return generic
	}
	var sensitive func(any) bool
	sensitive = func(value any) bool {
		switch v := value.(type) {
		case string:
			return contains(v)
		case json.Number:
			return contains(v.String())
		case bool:
			return contains(strconv.FormatBool(v))
		case map[string]any:
			for k, item := range v {
				if contains(k) || sensitive(item) {
					return true
				}
			}
		case []any:
			for _, item := range v {
				if sensitive(item) {
					return true
				}
			}
		}
		return false
	}
	payloads := []json.RawMessage{req.State}
	for _, raw := range req.Questions {
		var question map[string]json.RawMessage
		if json.Unmarshal(raw, &question) != nil {
			return generic
		}
		payloads = append(payloads, question["instructions"], question["criteria"])
	}
	for _, raw := range payloads {
		var value any
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if contains(string(raw)) || (decoder.Decode(&value) == nil && sensitive(value)) {
			return generic
		}
	}
	return message
}
