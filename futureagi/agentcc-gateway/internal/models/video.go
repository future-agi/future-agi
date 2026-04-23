package models

// VideoGenerationRequest is the provider-facing video generation request.
type VideoGenerationRequest struct {
	Model               string          `json:"model"`
	Prompt              string          `json:"prompt"`
	InputImage          *InputImageData `json:"input_image,omitempty"`
	Duration            float64         `json:"duration,omitempty"`
	Width               int             `json:"width,omitempty"`
	Height              int             `json:"height,omitempty"`
	FPS                 int             `json:"fps,omitempty"`
	NumVariants         int             `json:"n,omitempty"`
	Style               string          `json:"style,omitempty"`
	AspectRatio         string          `json:"aspect_ratio,omitempty"`
	RemixSourceVideoURL string          `json:"remix_source_video_url,omitempty"`
}

// InputImageData represents decoded image data for video generation.
type InputImageData struct {
	Data      []byte `json:"-"`
	MediaType string `json:"media_type"`
	URL       string `json:"url,omitempty"`
}

// VideoSubmitResponse is the provider's response after submitting a video job.
type VideoSubmitResponse struct {
	ProviderJobID    string `json:"provider_job_id"`
	Status           string `json:"status"`
	EstimatedSeconds int    `json:"estimated_seconds,omitempty"`
}

// VideoStatusResponse is the provider's response for video job status.
type VideoStatusResponse struct {
	ProviderJobID string                `json:"provider_job_id"`
	Status        string                `json:"status"`
	Progress      int                   `json:"progress"`
	Videos        []ProviderVideoOutput `json:"videos,omitempty"`
	Error         string                `json:"error,omitempty"`
}

// ProviderVideoOutput represents a single video output from a provider.
type ProviderVideoOutput struct {
	URL             string  `json:"url"`
	ContentType     string  `json:"content_type"`
	DurationSeconds float64 `json:"duration_seconds"`
	Width           int     `json:"width"`
	Height          int     `json:"height"`
	FileSizeBytes   int64   `json:"file_size_bytes"`
}
