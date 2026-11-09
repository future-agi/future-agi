package gemini

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/gauth"
)

type Provider struct {
	id            string
	baseURL       string
	apiKey        string
	vertexAI      bool
	tokenProvider *gauth.TokenProvider
	httpClient    *http.Client
	slots         *admission.Limiter
	headers       map[string]string
}

func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	errorCredentials := common.HeldErrorCredentials(cfg.Headers, cfg.APIKey, cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey, cfg.AWSSessionToken)

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
			Transport:     transport,
			Timeout:       timeout,
			CheckRedirect: keepCredentialInOrigin,
		},
		slots:   admission.New(id, cfg.MaxConcurrent, cfg.QueueTimeout),
		headers: cfg.Headers,
	}

	if vertexAI && (cfg.CredentialsFile != "" || cfg.ServiceAccountJSON != "") {
		var tp *gauth.TokenProvider
		var err error
		if cfg.ServiceAccountJSON != "" {
			tp, err = gauth.NewTokenProviderJSON([]byte(cfg.ServiceAccountJSON), gauth.ScopeCloudPlatform, errorCredentials...)
		} else {
			tp, err = gauth.NewTokenProvider(cfg.CredentialsFile, gauth.ScopeCloudPlatform, errorCredentials...)
		}
		if err != nil {
			p.slots.Close()
			return nil, common.ExactError(fmt.Errorf("gemini: vertex ai credentials: %w", err), errorCredentials...)
		}
		p.tokenProvider = tp
	}

	return p, nil
}

func (p *Provider) ID() string { return p.id }

// UpstreamModelName is the id every translated call sends the host for
// model (providers.UpstreamModelNamer). The native GenAI pass-through sends
// the model it is given as it is.
func (p *Provider) UpstreamModelName(model string) string { return resolveModelName(model) }

// ClientTimeout reports the timeout on this provider's HTTP client. It bounds
// the whole exchange, a streamed body included, so it is a hard ceiling on any
// request this provider serves.
func (p *Provider) ClientTimeout() time.Duration { return p.httpClient.Timeout }

func (p *Provider) bearerToken() string {
	if p.tokenProvider != nil {
		return p.tokenProvider.Token()
	}
	return p.apiKey
}

// sentToken is the access token a request carried, so a rejection invalidates only that token.
func sentToken(req *http.Request) string {
	return strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
}

// maxRedirects is what net/http's own default policy allows.
const maxRedirects = 10

// keepCredentialInOrigin decides what happens to this provider's credential
// when the upstream redirects.
//
// Go carries a custom header through every redirect and only strips the ones
// it knows, Authorization and Cookie among them. Putting the key in
// x-goog-api-key therefore made it follow a redirect anywhere the upstream
// pointed, including to plaintext. A downgrade is refused outright; a move
// away from the configured origin keeps going without the credential, so the
// request fails on authentication rather than handing the key to whoever
// answered.
//
// Every hop is judged against via[0], the origin the operator configured and
// trusted with the key — not against the hop before it. Go rebuilds each
// redirected request from the original, so a header dropped on one hop comes
// back on the next: comparing neighbours let A -> B -> B restore the
// credential at B, because the second hop looked like it stayed put.
func keepCredentialInOrigin(req *http.Request, via []*http.Request) error {
	errorCredentials := common.ErrorCredentials(req)

	if len(via) >= maxRedirects {
		return common.ExactError(fmt.Errorf("stopped after %d redirects", maxRedirects), errorCredentials...)
	}
	origin := via[0].URL
	if origin.Scheme == "https" && req.URL.Scheme != "https" {
		return common.ExactError(fmt.Errorf("refusing a redirect from https to %s: it would send the provider credential in plaintext", req.URL.Scheme), errorCredentials...)
	}
	if req.URL.Scheme != origin.Scheme || req.URL.Host != origin.Host {
		req.Header.Del("x-goog-api-key")
		req.Header.Del("Authorization")
	}
	return nil
}

// setAPIKey puts this provider's credential on the request.
//
// The key goes in a header, never in the URL. A failed call surfaces as a
// *url.Error carrying the whole URL, and that error reaches the client's 502
// body and the request log — so a key in the query string is a key published
// to anyone who can read either. The native pass-through path has always used
// this header; everything else used to append ?key= and leak it.
func (p *Provider) setAPIKey(req *http.Request) {
	if p.vertexAI {
		if tok := p.bearerToken(); tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
		return
	}
	if p.apiKey != "" {
		req.Header.Set("x-goog-api-key", p.apiKey)
	}
}

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

// ChatCompletion sends a non-streaming request to Gemini.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	errorCredentials := common.HeldErrorCredentials(p.headers, p.apiKey)

	if err := RejectUnsupportedTools(req.Tools, errorCredentials...); err != nil {
		return nil, err
	}

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, err
	}
	defer p.releaseSemaphore()

	gr, model := translateRequest(req)

	body, err := json.Marshal(gr)
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("gemini: marshaling request: %v", err)), errorCredentials...)
	}

	url := buildURL(p.baseURL, model, false)

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, common.ExactAPIError(models.ErrInternal("gemini: creating request: "+withoutURL(err, errorCredentials...)), errorCredentials...)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	p.setAPIKey(httpReq)
	for k, v := range p.headers {
		httpReq.Header.Set(k, v)
	}

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, common.ExactAPIError(models.ErrGatewayTimeout("gemini: request timed out"), errorCredentials...)
		}
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(0, "gemini: request failed: "+withoutURL(err, errorCredentials...)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, common.ReadError(err, resp.StatusCode, "gemini: reading response", errorCredentials...)
	}

	if resp.StatusCode != http.StatusOK {
		if resp.StatusCode == http.StatusUnauthorized && p.tokenProvider != nil {
			p.tokenProvider.Invalidate(sentToken(httpReq))
		}
		return nil, parseGeminiError(resp.StatusCode, respBody, errorCredentials...)
	}

	var gr2 geminiResponse
	if err := json.Unmarshal(respBody, &gr2); err != nil {
		return nil, common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing response: %v", err)), errorCredentials...)
	}

	return translateResponse(&gr2, model, errorCredentials...), nil
}

// StreamChatCompletion sends a streaming request to Gemini.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	errorCredentials := common.HeldErrorCredentials(p.headers, p.apiKey)

	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		if err := RejectUnsupportedTools(req.Tools, errorCredentials...); err != nil {
			errs <- err
			return
		}

		if err := p.acquireSemaphore(ctx); err != nil {
			errs <- err
			return
		}
		defer p.releaseSemaphore()

		gr, model := translateRequest(req)

		body, err := json.Marshal(gr)
		if err != nil {
			errs <- common.ExactAPIError(models.ErrInternal(fmt.Sprintf("gemini: marshaling request: %v", err)), errorCredentials...)
			return
		}

		url := buildURL(p.baseURL, model, true)

		upstreamCtx, cancelUpstream := context.WithCancel(ctx)
		defer cancelUpstream()
		httpReq, err := http.NewRequestWithContext(upstreamCtx, "POST", url, bytes.NewReader(body))
		if err != nil {
			errs <- common.ExactAPIError(models.ErrInternal("gemini: creating request: "+withoutURL(err, errorCredentials...)), errorCredentials...)
			return
		}

		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "text/event-stream")
		p.setAPIKey(httpReq)
		for k, v := range p.headers {
			httpReq.Header.Set(k, v)
		}

		errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if common.TimedOut(ctx, err) {
				errs <- common.ExactAPIError(models.ErrGatewayTimeout("gemini: request timed out"), errorCredentials...)
				return
			}
			errs <- common.ExactAPIError(models.ErrUpstreamProvider(0, "gemini: request failed: "+withoutURL(err, errorCredentials...)), errorCredentials...)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			if resp.StatusCode == http.StatusUnauthorized && p.tokenProvider != nil {
				p.tokenProvider.Invalidate(sentToken(httpReq))
			}
			respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			if readErr != nil {
				errs <- common.ExactAPIError(models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("gemini: error (HTTP %d), failed to read body: %v", resp.StatusCode, readErr)), errorCredentials...)
				return
			}
			errs <- parseGeminiError(resp.StatusCode, respBody, errorCredentials...)
			return
		}

		models.MarkStreamAccepted(ctx)
		scanner := bufio.NewScanner(resp.Body)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

		state := newStreamState(model)

		for scanner.Scan() {
			line := scanner.Text()

			if line == "" || !strings.HasPrefix(line, "data: ") {
				continue
			}

			data := strings.TrimPrefix(line, "data: ")

			chunk, err := state.parseStreamData(data, errorCredentials...)
			if err != nil {
				if _, terminal := err.(*models.APIError); terminal {
					errs <- common.ExactError(err, errorCredentials...)
					return
				}
				slog.Warn("gemini: error parsing stream data",
					"error", common.ExactError(err, errorCredentials...),
					"provider", p.id,
				)
				continue
			}

			if chunk != nil && len(chunk.Choices) > 0 {
				finished := false
				for _, choice := range chunk.Choices {
					if choice.FinishReason != nil && *choice.FinishReason != "" {
						finished = true
						break
					}
				}
				select {
				case chunks <- *chunk:
				case <-ctx.Done():
					return
				}
				// Gemini may keep the HTTP stream open after its terminal candidate.
				// Close our stream as soon as the finish reason arrives so callers
				// receive their final message event without waiting for EOF.
				if finished {
					cancelUpstream()
					return
				}
			}
		}

		if err := scanner.Err(); err != nil {
			if ctx.Err() == nil {
				errs <- common.ReadError(err, 0, "gemini: stream read", errorCredentials...)
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
	errorCredentials := common.HeldErrorCredentials(p.headers, p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, err
	}
	// Semaphore released when caller closes the stream.

	url := p.buildNativeURL(model, "streamGenerateContent") + "?alt=sse"

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(reqBody))
	if err != nil {
		p.releaseSemaphore()
		return nil, 0, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("gemini: creating native stream request: %v", err)), errorCredentials...)
	}

	p.setNativeGenAIHeaders(httpReq, headers)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if common.TimedOut(ctx, err) {
			return nil, 0, common.ExactAPIError(models.ErrGatewayTimeout("gemini: streaming request timed out"), errorCredentials...)
		}
		return nil, 502, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: streaming request failed: %v", err)), errorCredentials...)
	}

	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		p.releaseSemaphore()
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		return nil, resp.StatusCode, common.NativeHTTPError(resp.StatusCode, respBody, errorCredentials...)
	}

	return &semaphoreReadCloser{ReadCloser: resp.Body, release: p.releaseSemaphore}, resp.StatusCode, nil
}

// doGenAIRequest is a shared helper for non-streaming native GenAI requests.
func (p *Provider) doGenAIRequest(ctx context.Context, model string, action string, reqBody []byte, headers map[string]string) ([]byte, int, error) {
	errorCredentials := common.HeldErrorCredentials(p.headers, p.apiKey)

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, err
	}
	defer p.releaseSemaphore()

	url := p.buildNativeURL(model, action)

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, common.ExactAPIError(models.ErrInternal(fmt.Sprintf("gemini: creating native request: %v", err)), errorCredentials...)
	}

	p.setNativeGenAIHeaders(httpReq, headers)

	errorCredentials = common.ErrorCredentials(httpReq, errorCredentials...)
	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, 0, common.ExactAPIError(models.ErrGatewayTimeout("gemini: native request timed out"), errorCredentials...)
		}
		return nil, 502, common.ExactAPIError(models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: native request failed: %v", err)), errorCredentials...)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, resp.StatusCode, common.ReadError(err, resp.StatusCode, "gemini: reading native response", errorCredentials...)
	}

	if resp.StatusCode >= 400 {
		respBody = []byte(privacy.ScrubExactCredential(string(respBody), errorCredentials...))
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
	p.slots.Close()
	p.httpClient.CloseIdleConnections()
	return nil
}
