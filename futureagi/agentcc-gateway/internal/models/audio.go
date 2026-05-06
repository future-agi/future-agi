package models

// SpeechRequest represents an OpenAI-compatible text-to-speech request.
type SpeechRequest struct {
	Model          string   `json:"model"`
	Input          string   `json:"input"`
	Voice          string   `json:"voice"`                    // "alloy"|"echo"|"fable"|"onyx"|"nova"|"shimmer"
	ResponseFormat string   `json:"response_format,omitempty"` // "mp3"|"opus"|"aac"|"flac"|"wav"|"pcm"
	Speed          *float64 `json:"speed,omitempty"`           // 0.25 to 4.0
}

// TranscriptionRequest represents an OpenAI-compatible audio transcription request.
type TranscriptionRequest struct {
	Model          string   `json:"model"`
	FileData       []byte   `json:"-"`                         // Audio file bytes (from multipart)
	FileName       string   `json:"-"`                         // Original filename
	Language       string   `json:"language,omitempty"`        // ISO-639-1
	ResponseFormat string   `json:"response_format,omitempty"` // "json"|"text"|"srt"|"verbose_json"|"vtt"
	Temperature    *float64 `json:"temperature,omitempty"`
}

// TranscriptionResponse represents an OpenAI-compatible transcription response.
type TranscriptionResponse struct {
	Text string `json:"text"`
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

// TranslationResponse represents an OpenAI-compatible translation response.
type TranslationResponse struct {
	Text string `json:"text"`
}

// AudioStreamChunk represents a single chunk in a streaming TTS response.
type AudioStreamChunk struct {
	Index       int    `json:"index"`
	ContentType string `json:"content_type"`
	Data        string `json:"data"` // base64-encoded audio bytes
	Done        bool   `json:"done,omitempty"`
}
