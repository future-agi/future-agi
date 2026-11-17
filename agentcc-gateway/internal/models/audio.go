package models

import (
	"encoding/json"
	"mime"
)

// SpeechRequest represents an OpenAI-compatible text-to-speech request.
type SpeechRequest struct {
	Model          string   `json:"model"`
	Input          string   `json:"input"`
	Voice          string   `json:"voice"`                     // "alloy"|"echo"|"fable"|"onyx"|"nova"|"shimmer"
	ResponseFormat string   `json:"response_format,omitempty"` // "mp3"|"opus"|"aac"|"flac"|"wav"|"pcm"
	Speed          *float64 `json:"speed,omitempty"`           // 0.25 to 4.0
}

// TranscriptionRequest represents an OpenAI-compatible audio transcription request.
type TranscriptionRequest struct {
	Model                  string   `json:"model"`
	FileData               []byte   `json:"-"`                         // Audio file bytes (from multipart)
	FileName               string   `json:"-"`                         // Original filename
	Language               string   `json:"language,omitempty"`        // ISO-639-1
	Prompt                 string   `json:"prompt,omitempty"`          // Optional text to guide spelling and style
	ResponseFormat         string   `json:"response_format,omitempty"` // "json"|"text"|"srt"|"verbose_json"|"vtt"
	Temperature            *float64 `json:"temperature,omitempty"`
	TimestampGranularities []string `json:"timestamp_granularities,omitempty"` // "word"|"segment"; verbose_json only
}

// TranscriptionResponse is a transcription or translation as the upstream
// returned it. Body goes back to the caller unchanged, because verbose_json,
// text, srt and vtt do not fit one struct; Text is lifted out for logs and traces.
type TranscriptionResponse struct {
	Text        string `json:"text"`
	Body        []byte `json:"-"`
	ContentType string `json:"-"`
}

// NewTranscriptionResponse keeps an upstream body for pass-through. Its text is
// the "text" field of a JSON body, or the whole body for text, srt and vtt.
func NewTranscriptionResponse(body []byte, contentType string) (*TranscriptionResponse, error) {
	resp := &TranscriptionResponse{Body: body, ContentType: contentType}
	if mediaType, _, err := mime.ParseMediaType(contentType); err == nil && mediaType == "application/json" {
		if err := json.Unmarshal(body, resp); err != nil {
			return nil, err
		}
		return resp, nil
	}
	resp.Text = string(body)
	return resp, nil
}

// TranslationRequest represents an OpenAI-compatible audio translation request.
// Translates audio in any language to English text.
type TranslationRequest struct {
	Model          string   `json:"model"`
	FileData       []byte   `json:"-"`                         // Audio file bytes (from multipart)
	FileName       string   `json:"-"`                         // Original filename
	Prompt         string   `json:"prompt,omitempty"`          // Optional text to guide translation
	ResponseFormat string   `json:"response_format,omitempty"` // "json"|"text"|"srt"|"verbose_json"|"vtt"
	Temperature    *float64 `json:"temperature,omitempty"`
}

// TranslationResponse has the same shape and pass-through rules as a transcription.
type TranslationResponse = TranscriptionResponse

// AudioStreamChunk represents a single chunk in a streaming TTS response.
type AudioStreamChunk struct {
	Index       int    `json:"index"`
	ContentType string `json:"content_type"`
	Data        string `json:"data"` // base64-encoded audio bytes
	Done        bool   `json:"done,omitempty"`
}
