package gemini

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// geminiSpeechSampleRate is what Gemini TTS produces when the MIME type names no rate.
const geminiSpeechSampleRate = 24000

// CreateSpeech sends a text-to-speech request via Gemini's generateContent endpoint
// with responseModalities: ["AUDIO"]. It implements the SpeechProvider interface.
//
// Gemini TTS uses the same generateContent endpoint as chat but configured with
// audio output modality. The response carries base64-encoded 16-bit mono PCM in
// the inlineData of its parts. Only "pcm" (the default) and "wav" can be served
// without an audio encoder; any other response_format is rejected rather than
// answered with bytes in a format the caller did not ask for.
func (p *Provider) CreateSpeech(ctx context.Context, req *models.SpeechRequest) (io.ReadCloser, string, error) {
	switch req.ResponseFormat {
	case "", "pcm", "wav":
	default:
		return nil, "", models.ErrBadRequest("unsupported_response_format",
			fmt.Sprintf("gemini: text-to-speech returns pcm or wav, not %q", req.ResponseFormat))
	}

	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, "", models.ErrGatewayTimeout("gemini: concurrency limit reached")
	}
	defer p.releaseSemaphore()

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
		return nil, "", models.ErrInternal(fmt.Sprintf("gemini: marshaling speech request: %v", err))
	}

	url := buildURL(p.baseURL, model, false)
	if !p.vertexAI && p.apiKey != "" {
		url += "?key=" + p.apiKey
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, "", models.ErrInternal(fmt.Sprintf("gemini: creating speech request: %v", err))
	}

	httpReq.Header.Set("Content-Type", "application/json")
	if p.vertexAI {
		if tok := p.bearerToken(); tok != "" {
			httpReq.Header.Set("Authorization", "Bearer "+tok)
		}
	}
	for k, v := range p.headers {
		httpReq.Header.Set(k, v)
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, "", models.ErrGatewayTimeout("gemini: speech request timed out")
		}
		return nil, "", models.ErrUpstreamProvider(0, fmt.Sprintf("gemini: speech request failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 50*1024*1024)) // 50MB — audio can be large
	if err != nil {
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: reading speech response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		if resp.StatusCode == http.StatusUnauthorized && p.tokenProvider != nil {
			p.tokenProvider.Invalidate(sentToken(httpReq))
		}
		return nil, "", parseGeminiError(resp.StatusCode, respBody)
	}

	var gemResp geminiResponse
	if err := json.Unmarshal(respBody, &gemResp); err != nil {
		return nil, "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("gemini: parsing speech response: %v", err))
	}

	pcm, mimeType, err := extractAudioFromResponse(&gemResp)
	if err != nil {
		return nil, "", err
	}

	sampleRate := geminiSpeechSampleRate
	if _, params, err := mime.ParseMediaType(mimeType); err == nil {
		if rate, err := strconv.Atoi(params["rate"]); err == nil && rate > 0 {
			sampleRate = rate
		}
	}

	if req.ResponseFormat == "wav" {
		return io.NopCloser(bytes.NewReader(wrapPCMInWAV(pcm, sampleRate))), "audio/wav", nil
	}
	return io.NopCloser(bytes.NewReader(pcm)), fmt.Sprintf("audio/L16;rate=%d", sampleRate), nil
}

// extractAudioFromResponse decodes and joins every audio inlineData part of the
// first candidate that has any; a long input can come back split across parts.
func extractAudioFromResponse(resp *geminiResponse) (pcm []byte, mimeType string, err error) {
	for _, candidate := range resp.Candidates {
		for _, part := range candidate.Content.Parts {
			if part.InlineData == nil || !isAudioMimeType(part.InlineData.MimeType) {
				continue
			}
			decoded, err := base64.StdEncoding.DecodeString(part.InlineData.Data)
			if err != nil {
				return nil, "", models.ErrUpstreamProvider(http.StatusBadGateway,
					fmt.Sprintf("gemini: decoding audio base64: %v", err))
			}
			if mimeType == "" {
				mimeType = part.InlineData.MimeType
			}
			pcm = append(pcm, decoded...)
		}
		if mimeType != "" {
			return pcm, mimeType, nil
		}
	}
	return nil, "", models.ErrUpstreamProvider(http.StatusBadGateway,
		"gemini: no audio data in TTS response")
}

// wrapPCMInWAV prefixes 16-bit mono PCM with a canonical 44-byte WAV header.
func wrapPCMInWAV(pcm []byte, sampleRate int) []byte {
	const channels, bitsPerSample = 1, 16
	byteRate := sampleRate * channels * bitsPerSample / 8
	out := make([]byte, 44, 44+len(pcm))
	copy(out[0:], "RIFF")
	binary.LittleEndian.PutUint32(out[4:], uint32(36+len(pcm)))
	copy(out[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(out[16:], 16)
	binary.LittleEndian.PutUint16(out[20:], 1) // PCM
	binary.LittleEndian.PutUint16(out[22:], channels)
	binary.LittleEndian.PutUint32(out[24:], uint32(sampleRate))
	binary.LittleEndian.PutUint32(out[28:], uint32(byteRate))
	binary.LittleEndian.PutUint16(out[32:], channels*bitsPerSample/8)
	binary.LittleEndian.PutUint16(out[34:], bitsPerSample)
	copy(out[36:], "data")
	binary.LittleEndian.PutUint32(out[40:], uint32(len(pcm)))
	return append(out, pcm...)
}
