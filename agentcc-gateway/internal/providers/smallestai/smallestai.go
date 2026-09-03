// Package smallestai implements the gateway Provider interface for Smallest AI's
// Waves (TTS) and Pulse (STT) speech models.
//
// Source: https://docs.smallest.ai
//
// TTS (Waves): POST {base_url}/tts — models lightning_v3.1, lightning_v3.1_pro,
// passed in the request body. Auth: Authorization: Bearer {api_key}.
//
// STT (Pulse): POST {base_url}/stt/ — unified endpoint for both pulse
// (multilingual, REST + WebSocket) and pulse-pro (English-only, REST only),
// selected via ?model= query param. Body is raw audio bytes.
//
// Smallest AI is a speech-only provider: it has no chat/completions API, so
// ChatCompletion, StreamChatCompletion, and ListModels are not supported.
package smallestai

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const defaultLanguage = "en"

// defaultSampleRate is used when the OpenAI-compatible SpeechRequest carries
// no sample-rate hint (the shared schema doesn't expose one).
const defaultSampleRate = 44100

const (
	maxRawPCMResponseBytes        = 50 * 1024 * 1024
	maxUnknownRawPCMResponseBytes = 8 * 1024 * 1024
)

// Provider implements the gateway Provider interface for Smallest AI.
type Provider struct {
	id         string
	baseURL    string // https://api.smallest.ai/waves/v1
	apiKey     string
	httpClient *http.Client
	semaphore  chan struct{}
}

// New creates a new Smallest AI provider.
func New(id string, cfg config.ProviderConfig) (*Provider, error) {
	timeout := cfg.DefaultTimeout
	if timeout == 0 {
		timeout = 30 * time.Second
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
	if cfg.SkipTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec
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

func (p *Provider) setAuth(req *http.Request) {
	if p.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+p.apiKey)
	}
	req.Header.Set("X-Source", "future-agi")
}

// resolveModel strips a "provider/" prefix, leaving the bare model id
// (e.g. "lightning_v3.1", "pulse", "pulse-pro").
func resolveModel(model string) string {
	if idx := strings.Index(model, "/"); idx > 0 {
		return model[idx+1:]
	}
	return model
}

func defaultVoiceForModel(model string) string {
	if strings.Contains(model, "pro") {
		return "rhea"
	}
	return "quinn"
}

// ChatCompletion is not supported — Smallest AI has no chat/completions API.
func (p *Provider) ChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, models.ErrBadRequest("unsupported_operation",
		"smallest_ai provider supports audio speech and transcription only, not chat completions")
}

// StreamChatCompletion is not supported — Smallest AI has no chat/completions API.
func (p *Provider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk)
	errs := make(chan error, 1)
	errs <- models.ErrBadRequest("unsupported_operation",
		"smallest_ai provider supports audio speech and transcription only, not chat completions")
	close(chunks)
	close(errs)
	return chunks, errs
}

// ListModels is not implemented — Smallest AI's model catalog is static and
// already configured via litellm.json / provider config, matching the
// convention used by other providers without a models-listing API (e.g.
// cohere, bedrock).
func (p *Provider) ListModels(ctx context.Context) ([]models.ModelObject, error) {
	return nil, nil
}

// CreateSpeech sends a text-to-speech request to Smallest AI Waves and returns an audio stream.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	model := resolveModel(req.Model)

	voice := req.Voice
	if voice == "" {
		voice = defaultVoiceForModel(model)
	}

	outputFormat := req.ResponseFormat
	if outputFormat == "" {
		outputFormat = "wav"
	}
	if !supportedOutputFormat(outputFormat) {
		p.releaseSemaphore()
		return nil, "", models.ErrBadRequest("unsupported_response_format",
			"smallest_ai speech supports wav, pcm, mp3, ulaw, and alaw response formats")
	}

	speed := 1.0
	if req.Speed != nil {
		speed = *req.Speed
	}

	payload := map[string]any{
		"text":          req.Input,
		"voice_id":      voice,
		"model":         model,
		"sample_rate":   defaultSampleRate,
		"speed":         speed,
		"language":      defaultLanguage,
		"output_format": outputFormat,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("marshaling speech request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/tts", bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "audio/"+outputFormat)
	p.setAuth(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, "", models.ErrGatewayTimeout("speech request timed out")
		}
		return nil, "", models.ErrUpstreamProvider(0, fmt.Sprintf("speech request failed: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		defer func() { _ = resp.Body.Close() }()
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
		contentType = "audio/" + outputFormat
	}

	bufferedBody := bufio.NewReader(resp.Body)
	if _, peekErr := bufferedBody.Peek(1); peekErr != nil {
		_ = resp.Body.Close()
		p.releaseSemaphore()
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
			"speech response contained no audio data")
	}
	if outputFormat == "wav" {
		header, _ := bufferedBody.Peek(4)
		if !bytes.Equal(header, []byte("RIFF")) {
			if resp.ContentLength > maxRawPCMResponseBytes {
				_ = resp.Body.Close()
				p.releaseSemaphore()
				return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
					"raw PCM speech response exceeded 50 MiB")
			}
			if resp.ContentLength > 0 {
				if resp.ContentLength%2 != 0 {
					_ = resp.Body.Close()
					p.releaseSemaphore()
					return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
						"raw PCM speech response must contain an even number of bytes")
				}
				reader := io.MultiReader(
					bytes.NewReader(wavHeader(int(resp.ContentLength), defaultSampleRate)),
					bufferedBody,
				)
				return &semaphoreReadCloser{
					ReadCloser: &readerReadCloser{Reader: reader, Closer: resp.Body},
					release:    p.releaseSemaphore,
				}, "audio/wav", nil
			}

			rawPCM, readErr := io.ReadAll(io.LimitReader(bufferedBody, maxUnknownRawPCMResponseBytes+1))
			_ = resp.Body.Close()
			if readErr != nil {
				p.releaseSemaphore()
				return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
					fmt.Sprintf("reading raw PCM speech response: %v", readErr))
			}
			if len(rawPCM) > maxUnknownRawPCMResponseBytes {
				p.releaseSemaphore()
				return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
					"raw PCM speech response with unknown length exceeded 8 MiB")
			}
			if len(rawPCM)%2 != 0 {
				p.releaseSemaphore()
				return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
					"raw PCM speech response must contain an even number of bytes")
			}

			wavBytes := wrapPCMInWAV(rawPCM, defaultSampleRate)
			return &semaphoreReadCloser{
				ReadCloser: io.NopCloser(bytes.NewReader(wavBytes)),
				release:    p.releaseSemaphore,
			}, "audio/wav", nil
		}
		if _, peekErr := bufferedBody.Peek(45); peekErr != nil {
			_ = resp.Body.Close()
			p.releaseSemaphore()
			return nil, "", models.ErrUpstreamProvider(resp.StatusCode,
				"WAV speech response contained no audio payload")
		}
		contentType = "audio/wav"
	}

	return &semaphoreReadCloser{
		ReadCloser: &readerReadCloser{Reader: bufferedBody, Closer: resp.Body},
		release:    p.releaseSemaphore,
	}, contentType, nil
}

type readerReadCloser struct {
	io.Reader
	io.Closer
}

func wrapPCMInWAV(rawPCM []byte, sampleRate int) []byte {
	header := wavHeader(len(rawPCM), sampleRate)
	return append(header, rawPCM...)
}

func wavHeader(dataSize, sampleRate int) []byte {
	const channels, bitsPerSample = 1, 16
	header := make([]byte, 44)
	copy(header[0:4], "RIFF")
	binary.LittleEndian.PutUint32(header[4:8], uint32(36+dataSize)) //nolint:gosec
	copy(header[8:16], "WAVEfmt ")
	binary.LittleEndian.PutUint32(header[16:20], 16)
	binary.LittleEndian.PutUint16(header[20:22], 1)
	binary.LittleEndian.PutUint16(header[22:24], channels)
	binary.LittleEndian.PutUint32(header[24:28], uint32(sampleRate)) //nolint:gosec
	byteRate := sampleRate * channels * bitsPerSample / 8
	binary.LittleEndian.PutUint32(header[28:32], uint32(byteRate)) //nolint:gosec
	blockAlign := channels * bitsPerSample / 8
	binary.LittleEndian.PutUint16(header[32:34], uint16(blockAlign)) //nolint:gosec
	binary.LittleEndian.PutUint16(header[34:36], bitsPerSample)
	copy(header[36:40], "data")
	binary.LittleEndian.PutUint32(header[40:44], uint32(dataSize)) //nolint:gosec
	return header
}

func supportedOutputFormat(format string) bool {
	switch format {
	case "wav", "pcm", "mp3", "ulaw", "alaw":
		return true
	default:
		return false
	}
}

// semaphoreReadCloser wraps an io.ReadCloser to release a semaphore on Close.
type semaphoreReadCloser struct {
	io.ReadCloser
	release func()
	once    sync.Once
	err     error
}

func (s *semaphoreReadCloser) Close() error {
	s.once.Do(func() {
		s.err = s.ReadCloser.Close()
		s.release()
	})
	return s.err
}

// smallestAITranscriptionResponse mirrors the /stt/ REST response shape.
type smallestAITranscriptionResponse struct {
	Status        string `json:"status"`
	Transcription string `json:"transcription"`
}

// CreateTranscription sends an audio transcription request to Smallest AI Pulse.
func (p *Provider) CreateTranscription(ctx context.Context, req *models.TranscriptionRequest) (*models.TranscriptionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout("provider concurrency limit reached")
	}
	defer p.releaseSemaphore()

	model := resolveModel(req.Model)

	language := req.Language
	if language == "" {
		language = defaultLanguage
	}

	endpoint, err := url.Parse(p.baseURL + "/stt/")
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("parsing transcription endpoint: %v", err))
	}
	query := endpoint.Query()
	query.Set("model", model)
	query.Set("language", language)
	endpoint.RawQuery = query.Encode()

	httpReq, err := http.NewRequestWithContext(ctx, "POST", endpoint.String(), bytes.NewReader(req.FileData))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("creating transcription request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/octet-stream")
	p.setAuth(httpReq)

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("transcription request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("transcription request failed: %v", err))
	}
	defer func() { _ = resp.Body.Close() }()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("reading transcription response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseProviderError(resp.StatusCode, respBody)
	}

	var result smallestAITranscriptionResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("parsing transcription response: %v", err))
	}

	return &models.TranscriptionResponse{Text: result.Transcription}, nil
}

// Close releases resources.
func (p *Provider) Close() error {
	p.httpClient.CloseIdleConnections()
	return nil
}

func parseProviderError(status int, body []byte) *models.APIError {
	var errResp models.ErrorResponse
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Error.Message != "" {
		return &models.APIError{
			Status:  mapProviderStatus(status),
			Type:    errResp.Error.Type,
			Code:    errResp.Error.Code,
			Message: errResp.Error.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("provider error (HTTP %d): %s", status, msg))
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
