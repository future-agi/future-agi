package gemini

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// CreateSpeech sends a text-to-speech request via Gemini's generateContent endpoint
// with responseModalities: ["AUDIO"]. It implements the SpeechProvider interface.
//
// Gemini TTS uses the same generateContent endpoint as chat but configured with
// audio output modality. The response contains base64-encoded PCM16 audio at 24kHz
// in the inlineData field of response parts.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	// NOTE: semaphore is released when the caller closes the returned ReadCloser.

	model := resolveModelName(req.Model)

	// Build the Gemini generateContent request with audio modality.
	gemReq := geminiRequest{
		Contents: []geminiContent{
			{
				Role: "user",
				Parts: []geminiPart{
					{Text: req.Input},
				},
			},
		},
		GenerationConfig: &geminiGenerationConfig{
			ResponseModalities: []string{"AUDIO"},
			SpeechConfig: &geminiSpeechConfig{
				VoiceConfig: &geminiVoiceConfig{
					PrebuiltVoiceConfig: &geminiPrebuiltVoiceConfig{
						VoiceName: mapVoiceToGemini(req.Voice),
					},
				},
			},
		},
	}

	body, err := json.Marshal(gemReq)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("gemini: marshaling speech request: %v", err))
	}

	url := buildURL(p.baseURL, model, false)
	if !p.vertexAI && p.apiKey != "" {
		url += "?key=" + p.apiKey
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrInternal(fmt.Sprintf("gemini: creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.vertexAI && p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}
	for k, v := range p.headers {
		httpReq.Header.Set(k, v)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		p.releaseSemaphore()
		if ctx.Err() != nil {
			return nil, "", models.ErrGatewayTimeout("gemini: speech request timed out")
		}
		return nil, "", models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: speech request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 50*1024*1024)) // 50MB — audio can be large
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading speech response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		p.releaseSemaphore()
		return nil, "", parseGeminiError(resp.StatusCode, respBody)
	}

	var gemResp geminiResponse
	if err := json.Unmarshal(respBody, &gemResp); err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing speech response: %v", err))
	}

	// Extract audio data from the response.
	audioData, mimeType, err := extractAudioFromResponse(&gemResp)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", err
	}

	// Decode base64 audio data into raw bytes.
	decoded, err := base64.StdEncoding.DecodeString(audioData)
	if err != nil {
		p.releaseSemaphore()
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: decoding audio base64: %v", err))
	}

	// Build content type. Gemini returns audio/L16 (PCM16) at 24000 Hz.
	contentType := mimeType
	if contentType == "" {
		contentType = "audio/L16;rate=24000"
	}

	// Wrap decoded bytes in a ReadCloser that releases the semaphore on close.
	reader := io.NopCloser(bytes.NewReader(decoded))
	return &semaphoreReadCloser{ReadCloser: reader, release: p.releaseSemaphore}, contentType, nil
}

// extractAudioFromResponse finds and returns the first audio inlineData part
// from a Gemini generateContent response.
func extractAudioFromResponse(resp *geminiResponse) (data string, mimeType string, err error) {
	for _, candidate := range resp.Candidates {
		for _, part := range candidate.Content.Parts {
			if part.InlineData != nil && isAudioMimeType(part.InlineData.MimeType) {
				return part.InlineData.Data, part.InlineData.MimeType, nil
			}
		}
	}
	return "", "", models.ErrUpstreamProvider(http.StatusBadGateway,
		"gemini: no audio data in TTS response")
}
