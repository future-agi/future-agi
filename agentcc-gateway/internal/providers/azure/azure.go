package azure

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

const (
	// defaultAPIVersion is used when the provider config sets no api-version.
	defaultAPIVersion = "2024-10-21"
	// minAPIVersionMaxCompletionTokens is the first Azure api-version that accepts
	// max_completion_tokens. Earlier ones reject it.
	minAPIVersionMaxCompletionTokens = "2024-09-01"
)

// Provider implements the Provider interface for Azure OpenAI.
// Azure uses a different URL format: /openai/deployments/{deployment}/chat/completions?api-version={version}
type Provider struct {
	id         string
	baseURL    string // https://{resource}.openai.azure.com
	apiKey     string
	apiVersion string
	authType   string // "api-key" (default) or "bearer" (Azure AD)
	headers    common.HeaderDefaults
	httpClient *http.Client
	slots      *admission.Limiter
}

// New creates a new Azure OpenAI provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	headers, err := common.NewHeaderDefaults(cfg.Headers, "api-version", "auth-type")
	if err != nil {
		return nil, err
	}
	timeout := cfg.DefaultTimeout
	if timeout == 0 {
		timeout = 60 * time.Second
	}

	// max_concurrent, conn_pool_size and queue_timeout come resolved from the
	// registry (local or remote defaults); admission and these defaults cover
	// an instance built from a config that left them unset.
	poolSize := cfg.ConnPoolSize
	if poolSize <= 0 {
		poolSize = admission.DefaultConnPoolSize
	}

	apiVersion := cfg.Headers["api-version"]
	if apiVersion == "" {
		apiVersion = defaultAPIVersion
	}

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
		DialContext:         cfg.DialContext,
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
		headers:    headers,
		httpClient: &http.Client{
			Transport: transport,
			Timeout:   timeout,
		},
		slots: admission.New(id, cfg.MaxConcurrent, cfg.QueueTimeout),
	}, nil
}

func (p *Provider) ID() string { return p.id }

// UpstreamModelName is the deployment every call names for model
// (providers.UpstreamModelNamer).
func (p *Provider) UpstreamModelName(model string) string { return resolveDeployment(model) }

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

// deploymentURL builds the Azure-specific URL for a deployment.
func (p *Provider) deploymentURL(deployment, endpoint string) string {
	return fmt.Sprintf("%s/openai/deployments/%s/%s?api-version=%s",
		p.baseURL, deployment, endpoint, p.apiVersion)
}

// setAuth sets Azure authentication headers.
// Supports both api-key (default) and bearer token (Azure AD) auth.
func (p *Provider) setAuth(req *http.Request) {
	p.headers.Apply(req)
	if p.apiKey == "" {
		return
	}
	if p.authType == "bearer" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	} else {
		req.Header.Set("api-key", p.apiKey)
	}
}

// normalizeMaxTokens rewrites max_tokens to max_completion_tokens, which reasoning
// deployments require and which Azure rejects alongside max_tokens, so max_tokens
// is always cleared.
//
// Keyed on the api-version rather than the model: Azure routes by deployment name,
// which the customer chooses freely (the model field in the body is ignored), so a
// model-name predicate is unreliable here. api-versions before
// minAPIVersionMaxCompletionTokens reject max_completion_tokens outright and are
// left untouched.
//
// Operates on the caller's copy; the original request is never modified.
func (p *Provider) normalizeMaxTokens(req *models.ChatCompletionRequest) {
	if req.MaxTokens == nil || !supportsMaxCompletionTokens(p.apiVersion) {
		return
	}
	if req.MaxCompletionTokens == nil {
		req.MaxCompletionTokens = req.MaxTokens
	}
	req.MaxTokens = nil
}

// supportsMaxCompletionTokens reports whether an Azure api-version accepts
// max_completion_tokens. Azure api-versions are date-ordered strings, so a
// lexicographic compare is sufficient; the newer non-date labels ("preview", "v1")
// sort above the cutoff, which is correct — they accept it too.
func supportsMaxCompletionTokens(apiVersion string) bool {
	return apiVersion >= minAPIVersionMaxCompletionTokens
}

// ChatCompletion sends a non-streaming chat completion request.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	reqCopy := *req
	reqCopy.Stream = false
	// Azure ignores the model field in the body — routing is via URL deployment name.
	p.normalizeMaxTokens(&reqCopy)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("marshaling request: %v", err)), errorCredentials...)
	}

	url := p.deploymentURL(deployment, "chat/completions")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("provider request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading provider response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody, errorCredentials...)
	}

	var result models.ChatCompletionResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing provider response: %v", err)), errorCredentials...)
	}

	return &result, nil
}

// StreamChatCompletion sends a streaming chat completion request.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

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

		deployment := resolveDeployment(req.Model)
		reqCopy := *req
		reqCopy.Stream = true
		p.normalizeMaxTokens(&reqCopy)

		body, err := json.Marshal(&reqCopy)
		if err != nil {
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("marshaling request: %v", err)), errorCredentials...)
			return
		}

		url := p.deploymentURL(deployment, "chat/completions")
		httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
		if err != nil {
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating request: %v", err)), errorCredentials...)
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		p.setAuth(httpReq)

		errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if common.TimedOut(ctx, err) {
				errs <- common.ExactAPIError(models.ErrGatewayTimeout("provider request timed out"), errorCredentials...)
				return
			}
			errs <- common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err)), errorCredentials...)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("provider error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr)), errorCredentials...)
				return
			}
			errs <- parseProviderError(resp.StatusCode, respBody, errorCredentials...)
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

			var envelope struct {
				Error json.RawMessage `json:"error"`
			}
			if json.Unmarshal([]byte(data), &envelope) == nil && len(envelope.Error) > 0 && string(envelope.Error) != "null" {
				errs <- parseProviderError(http.StatusBadGateway, []byte(data), errorCredentials...)
				return
			}
			var chunk models.StreamChunk
			if err := json.Unmarshal([]byte(data), &chunk); err != nil {
				slog.Warn("failed to parse stream chunk",
					"error", common.ExactError(err, errorCredentials...), "data", privacy.ScrubExactCredential(data, errorCredentials...), "provider", p.id)
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
				errs <- common.ReadError(err, 0, "stream read", errorCredentials...)
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models from Azure OpenAI.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	url := fmt.Sprintf("%s/openai/models?api-version=%s", p.baseURL, p.apiVersion)
	httpReq, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, common.ExactError(err, errorCredentials...)
	}
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		return nil, common.ExactError(err, errorCredentials...)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, common.ExactError(fmt.Errorf("list models returned status %d", resp.StatusCode), errorCredentials...)
	}

	var result models.ModelListResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, common.ExactError(err, errorCredentials...)
	}

	return result.Data, nil
}

// CreateEmbedding sends an embedding request to Azure OpenAI.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("marshaling embedding request: %v", err)), errorCredentials...)
	}

	url := p.deploymentURL(deployment, "embeddings")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating embedding request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("embedding request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("embedding request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading embedding response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody, errorCredentials...)
	}

	var result models.EmbeddingResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing embedding response: %v", err)), errorCredentials...)
	}

	return &result, nil
}

// CreateImage sends an image generation request to Azure OpenAI (DALL-E).
func (p *Provider) CreateImage(ctx context.Context, req *models.ImageRequest) (*models.ImageResponse, error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("marshaling image request: %v", err)), errorCredentials...)
	}

	url := p.deploymentURL(deployment, "images/generations")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating image request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("image request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("image request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading image response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody, errorCredentials...)
	}

	var result models.ImageResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing image response: %v", err)), errorCredentials...)
	}

	return &result, nil
}

// CreateSpeech sends a text-to-speech request to Azure OpenAI and returns an audio stream.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", err
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	deployment := resolveDeployment(req.Model)

	body, err := json.Marshal(req)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", common.ExactAPIError(models.ErrInternal(fmt.Sprintf("marshaling speech request: %v", err)), errorCredentials...)
	}

	url := p.deploymentURL(deployment, "audio/speech")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating speech request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if common.TimedOut(ctx, err) {
			return nil, "", common.ExactAPIError(models.ErrGatewayTimeout("speech request timed out"), errorCredentials...)
		}
		return nil, "", common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("speech request failed: %v", err)), errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if readErr != nil {
			return nil, "", common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode,
				fmt.Sprintf("speech error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr)), errorCredentials...)
		}
		return nil, "", parseProviderError(resp.StatusCode, respBody, errorCredentials...)
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
	errorCredentials := p.headers.ErrorCredentials(p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
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
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating multipart file: %v", err)), errorCredentials...)
	}
	if _, err := filePart.Write(req.FileData); err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("writing file data: %v", err)), errorCredentials...)
	}

	// Add model field (Azure may ignore this, but include for compatibility).
	writer.WriteField("model", deployment)

	// Add optional fields.
	if req.Language != "" {
		writer.WriteField("language", req.Language)
	}
	if req.Prompt != "" {
		writer.WriteField("prompt", req.Prompt)
	}
	if req.ResponseFormat != "" {
		writer.WriteField("response_format", req.ResponseFormat)
	}
	if req.Temperature != nil {
		writer.WriteField("temperature", fmt.Sprintf("%g", *req.Temperature))
	}
	for _, granularity := range req.TimestampGranularities {
		writer.WriteField("timestamp_granularities[]", granularity)
	}

	if err := writer.Close(); err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("closing multipart writer: %v", err)), errorCredentials...)
	}

	url := p.deploymentURL(deployment, "audio/transcriptions")
	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, &buf)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("creating transcription request: %v", err)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	p.setAuth(httpReq)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("transcription request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("transcription request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading transcription response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody, errorCredentials...)
	}

	result, err := models.NewTranscriptionResponse(respBody, resp.Header.Get("Content-Type"))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing transcription response: %v", err)), errorCredentials...)
	}
	return result, nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.slots.Close()
	p.httpClient.CloseIdleConnections()
	return nil
}

func parseProviderError(status int, body []byte, credentials ...string) *models.APIError {
	var errResp models.ErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		// Status is what the client gets (an upstream 5xx is a 502);
		// UpstreamStatus keeps the provider's own for the status lists.
		return common.ExactAPIError(&models.APIError{
			Status:         mapProviderStatus(status),
			Type:           errResp.Error.Type,
			Code:           errResp.Error.Code,
			Message:        errResp.Error.Message,
			UpstreamStatus: status,
			ProviderStatus: status,
		}, credentials...)
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	e := common.ExactAPIError(models.ErrUpstreamProvider(status, fmt.Sprintf("provider error (HTTP %d): %s", status, msg)), credentials...)
	e.ProviderStatus = status // A real HTTP refusal, not a synthetic gateway failure.
	return e
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
