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
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// Provider implements the Provider interface for OpenAI-compatible APIs.
type Provider struct {
	id                 string
	baseURL            string
	pathPrefix         string
	apiKey             string
	headers            common.HeaderDefaults
	httpClient         *http.Client
	decisionHTTPClient *http.Client
	slots              *admission.Limiter
	// openAIAPI reports whether baseURL points at the official OpenAI API, whose
	// max_tokens/max_completion_tokens handling differs from other OpenAI-format
	// upstreams. See normalizeMaxTokens.
	openAIAPI bool
	// Explicitly typed OpenRouter proxies use the same continuation format.
	openRouterAPI bool
}

// officialAPIHost is the host of the official OpenAI API. Other upstreams speak
// the same wire format but not the same parameter contract.
const officialAPIHost = "api.openai.com"

// Image-generation deltas carry whole base64 images rather than text-sized
// tokens. Match the non-streaming response budget while retaining a hard per-
// line bound (including the newline); the scanner still starts with only 64 KiB.
const maxChatCompletionStreamTokenBytes = 10 * 1024 * 1024

// isOfficialAPI reports whether baseURL points at the official OpenAI API.
func isOfficialAPI(baseURL string) bool {
	u, err := url.Parse(baseURL)
	if err != nil {
		return false
	}
	return strings.EqualFold(u.Hostname(), officialAPIHost)
}

// New creates a new OpenAI-compatible provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	headers, err := common.NewHeaderDefaults(cfg.Headers)
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

	transport := &http.Transport{
		MaxIdleConns:        poolSize,
		MaxIdleConnsPerHost: poolSize,
		IdleConnTimeout:     90 * time.Second,
		ForceAttemptHTTP2:   true,
		DialContext:         cfg.DialContext,
		Proxy:               cfg.Proxy,
	}
	if cfg.SkipTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // user-opted skip
		slog.Warn("TLS verification disabled for provider", "provider", id)
	}

	client := &http.Client{Transport: transport, Timeout: timeout}
	decisionClient := client
	if cfg.DecisionProxy != nil || cfg.DecisionDialContext != nil {
		decisionTransport := transport.Clone()
		if cfg.DecisionProxy != nil {
			decisionTransport.Proxy = cfg.DecisionProxy
		}
		if cfg.DecisionDialContext != nil {
			decisionTransport.DialContext = cfg.DecisionDialContext
		}
		decisionClient = &http.Client{Transport: decisionTransport, Timeout: timeout}
	}

	return &Provider{
		id:                 id,
		baseURL:            strings.TrimRight(cfg.BaseURL, "/"),
		pathPrefix:         cfg.EffectiveAPIPathPrefix(),
		apiKey:             cfg.APIKey,
		headers:            headers,
		httpClient:         client,
		decisionHTTPClient: decisionClient,
		slots:              admission.New(id, cfg.MaxConcurrent, cfg.QueueTimeout),
		openAIAPI:          isOfficialAPI(cfg.BaseURL),
		openRouterAPI:      cfg.Type == "openrouter",
	}, nil
}

// normalizeMaxTokens rewrites max_tokens to max_completion_tokens for the official
// OpenAI API, which rejects max_tokens on reasoning models but accepts
// max_completion_tokens on every chat model. Setting both is also rejected, so
// max_tokens is always cleared.
//
// Deliberately keyed on the upstream, not the model name: the set of models that
// reject max_tokens keeps growing, while "the official API accepts
// max_completion_tokens" has held for every model. Other OpenAI-format upstreams
// (vLLM, Ollama, and other self-hosted servers) are left untouched — several only
// understand max_tokens.
//
// Operates on the caller's copy; the original request is never modified.
func (p *Provider) normalizeMaxTokens(req *models.ChatCompletionRequest) {
	if !p.openAIAPI || req.MaxTokens == nil {
		return
	}
	if req.MaxCompletionTokens == nil {
		req.MaxCompletionTokens = req.MaxTokens
	}
	req.MaxTokens = nil
}

// endpoint builds a full upstream URL for a path like "/v1/ocr", swapping the
// assumed "/v1" for whatever version segment this provider actually uses.
func (p *Provider) endpoint(path string) string {
	return config.JoinEndpoint(p.baseURL, p.pathPrefix, path)
}

func (p *Provider) ID() string { return p.id }

// UsesOpenRouter describes this instance's continuation representation, not
// the billing mode or another provider configuration with the same ID.
func (p *Provider) UsesOpenRouter() bool { return p.openRouterAPI || p.IsOpenRouter() }

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

// OCR forwards a /v1/ocr request to the provider's upstream. Used for
// Mistral (which serves POST https://api.mistral.ai/v1/ocr) when aliased to
// the openai-compat provider via the mistral preset. Returns the response
// body parsed into the shared OCRResponse shape.
func (p *Provider) OCR(ctx context.Context, req *models.OCRRequest) (*models.OCRResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
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
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 25*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading provider response")
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
		return nil, err
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
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading provider response")
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
		return nil, err
	}
	defer p.releaseSemaphore()

	// Strip provider prefix from model name.
	actualModel := resolveModelName(req.Model)

	// Project continuation state for this selected instance, not the model or ID.
	reqCopy := common.ChatRequestForUpstream(req, p.UsesOpenRouter())
	reqCopy.Model = actualModel
	reqCopy.Stream = false
	p.normalizeMaxTokens(&reqCopy)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/chat/completions"), bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("provider request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("provider request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024)) // 10MB limit
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading provider response")
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
	// Capture routing fields before returning, not after the goroutine waits
	// for admission. Nested payloads remain read-only for the call's lifetime.
	snapshot := *req
	req = &snapshot
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

		actualModel := resolveModelName(req.Model)
		reqCopy := common.ChatRequestForUpstream(req, p.UsesOpenRouter())
		reqCopy.Model = actualModel
		reqCopy.Stream = true
		// Always collect accounting evidence, independently of whether the
		// caller opted into usage chunks. Replace only the outbound copy's
		// options; never mutate the caller's shared StreamOptions pointer.
		reqCopy.StreamOptions = &models.StreamOptions{IncludeUsage: true}
		p.normalizeMaxTokens(&reqCopy)

		body, err := json.Marshal(&reqCopy)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("marshaling request: %v", err))
			return
		}

		httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/chat/completions"), bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("creating request: %v", err))
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		if credential := p.credential(ctx); credential != "" {
			httpReq.Header.Set("Authorization", "Bearer "+credential)
		}

		resp, err := p.do(httpReq)
		if err != nil {
			if common.TimedOut(ctx, err) {
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

		models.MarkStreamAccepted(ctx)
		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), maxChatCompletionStreamTokenBytes)

		for scanner.Scan() {
			line := scanner.Text()

			if line == "" {
				continue
			}

			if !strings.HasPrefix(line, "data:") {
				continue
			}

			data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))

			if data == "[DONE]" {
				return
			}

			if failure, evidence := streamErrorEnvelope([]byte(data)); failure != nil {
				if evidence.Usage != nil || evidence.UpstreamProvider != "" || evidence.ID != "" || evidence.Model != "" {
					select {
					case chunks <- evidence:
					case <-ctx.Done():
						return
					}
				}
				errs <- failure
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
				errs <- common.ReadError(err, 0, "stream read")
			}
		}
	}()

	return chunks, errs
}

// ListModels returns available models from this provider.
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	httpReq, err := http.NewRequestWithContext(ctx, "GET", p.endpoint("/v1/models"), nil)
	if err != nil {
		return nil, err
	}

	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
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
		return nil, err
	}
	defer p.releaseSemaphore()

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling image request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/images/generations"), bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating image request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("image request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("image request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading image response")
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
		return nil, "", err
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("marshaling speech request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/audio/speech"), bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if common.TimedOut(ctx, err) {
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
		return nil, err
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
		return nil, models.ErrInternal(fmt.Sprintf("closing multipart writer: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/audio/transcriptions"), &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating transcription request: %v", err))
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("transcription request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("transcription request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading transcription response")
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	result, err := models.NewTranscriptionResponse(respBody, resp.Header.Get("Content-Type"))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing transcription response: %v", err))
	}
	return result, nil
}

// CreateResponse sends a Responses API request and returns the raw JSON response.
func (p *Provider) CreateResponse(ctx context.Context, reqBody []byte) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, err
	}
	defer p.releaseSemaphore()

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/responses"), bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating responses request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, 0, models.ErrGatewayTimeout("responses request timed out")
		}
		return nil, 0, models.ErrUpstreamProvider(0, fmt.Sprintf("responses request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, common.ReadError(err, resp.StatusCode, "reading responses body")
	}

	if resp.StatusCode != http.StatusOK {
		return nil, resp.StatusCode, parseProviderError(resp.StatusCode, respBody)
	}

	return respBody, resp.StatusCode, nil
}

// StreamResponse sends a streaming Responses API request and returns the SSE stream.
func (p *Provider) StreamResponse(ctx context.Context, reqBody []byte) (io.ReadCloser, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, err
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/responses"), bytes.NewReader(reqBody))
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating streaming responses request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "text/event-stream")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if common.TimedOut(ctx, err) {
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
		return nil, err
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

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/audio/translations"), &buf)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating translation request: %v", err))
	}

	httpReq.Header.Set("Content-Type", writer.FormDataContentType())
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("translation request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("translation request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading translation response")
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	result, err := models.NewTranscriptionResponse(respBody, resp.Header.Get("Content-Type"))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing translation response: %v", err))
	}
	return result, nil
}

// CreateEmbedding sends an embedding request.
func (p *Provider) CreateEmbedding(ctx context.Context, req *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	reqCopy := *req
	reqCopy.Model = resolveModelName(req.Model)

	body, err := json.Marshal(&reqCopy)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling embedding request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.endpoint("/v1/embeddings"), bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating embedding request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, models.ErrGatewayTimeout("embedding request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("embedding request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "reading embedding response")
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

// ProxyAssistantsRequest forwards a non-streaming Assistants API request to OpenAI.
func (p *Provider) ProxyAssistantsRequest(ctx context.Context, method string, path string, body []byte, queryParams url.Values) ([]byte, int, http.Header, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, nil, err
	}
	defer p.releaseSemaphore()

	upstreamURL := p.endpoint(path)
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
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, 0, nil, models.ErrGatewayTimeout("assistants request timed out")
		}
		return nil, 0, nil, models.ErrUpstreamProvider(0, fmt.Sprintf("assistants request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, nil, common.ReadError(err, resp.StatusCode, "reading assistants response")
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
		return nil, 0, nil, err
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	upstreamURL := p.endpoint(path)
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
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if common.TimedOut(ctx, err) {
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
		return nil, 0, nil, err
	}
	defer p.releaseSemaphore()

	upstreamURL := p.endpoint(path)
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
	if credential := p.credential(ctx); credential != "" {
		httpReq.Header.Set("Authorization", "Bearer "+credential)
	}

	resp, err := p.do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, 0, nil, models.ErrGatewayTimeout("vector stores request timed out")
		}
		return nil, 0, nil, models.ErrUpstreamProvider(0, fmt.Sprintf("vector stores request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, nil, common.ReadError(err, resp.StatusCode, "reading vector stores response")
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
	p.slots.Close()
	p.httpClient.CloseIdleConnections()
	if p.decisionHTTPClient != p.httpClient {
		p.decisionHTTPClient.CloseIdleConnections()
	}
	return nil
}

func parseProviderError(status int, body []byte) (result *models.APIError) {
	// HTTP failures can carry paid generation evidence too. Decode only the
	// known fields; never keep the raw error body in billing records.
	defer func() {
		if result != nil {
			result.ProviderRejected = status >= 400 && status <= 599
		}
		var response models.ChatCompletionResponse
		if result != nil && json.Unmarshal(body, &response) == nil {
			result.BillingEvidence = &models.ProviderBillingEvidence{
				Generation: models.GenerationFromResponse(&response), Usage: response.Usage,
			}
		}
	}()
	// Try to parse as OpenAI error format.
	var errResp models.ErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		// Status is what the client gets (an upstream 5xx is a 502);
		// UpstreamStatus keeps the provider's own for the status lists.
		return &models.APIError{
			Status:         mapProviderStatus(status),
			Type:           errResp.Error.Type,
			Code:           errResp.Error.Code,
			Message:        errResp.Error.Message,
			UpstreamStatus: status,
			ProviderStatus: status,
		}
	}

	// Fallback: use raw body as message.
	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	e := models.ErrUpstreamProvider(status, fmt.Sprintf("provider error (HTTP %d): %s", status, msg))
	// This fallback read a real HTTP error, not a synthetic gateway failure.
	e.ProviderStatus = status
	return e
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
