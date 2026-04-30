package video

import (
	"sync"
	"time"
)

// VideoJobStatus constants.
const (
	StatusQueued     = "queued"
	StatusInProgress = "in_progress"
	StatusCompleted  = "completed"
	StatusFailed     = "failed"
	StatusCancelled  = "cancelled"
)

// VideoJob represents a video generation job.
type VideoJob struct {
	ID             string            `json:"id"`
	OrgID          string            `json:"org_id,omitempty"`
	Status         string            `json:"status"`
	Model          string            `json:"model"`
	Provider       string            `json:"provider,omitempty"`
	ProviderJobID  string            `json:"-"`
	Prompt         string            `json:"prompt"`
	Duration       float64           `json:"duration,omitempty"`
	Size           string            `json:"size,omitempty"`
	FPS            int               `json:"fps,omitempty"`
	NumVariants    int               `json:"n,omitempty"`
	Style          string            `json:"style,omitempty"`
	AspectRatio    string            `json:"aspect_ratio,omitempty"`
	Progress       int               `json:"progress"`
	Videos         []VideoOutput     `json:"videos,omitempty"`
	Error          *VideoError       `json:"error,omitempty"`
	Usage          *VideoUsage       `json:"usage,omitempty"`
	Cost           float64           `json:"cost,omitempty"`
	ClientMetadata map[string]string `json:"metadata,omitempty"`
	RemixSourceID  string            `json:"remix_source,omitempty"`
	CreatedAt      time.Time         `json:"created_at"`
	StartedAt      *time.Time        `json:"started_at,omitempty"`
	CompletedAt    *time.Time        `json:"completed_at,omitempty"`
	ExpiresAt      time.Time         `json:"expires_at"`
	PollFailures   int               `json:"-"`
	mu             sync.Mutex
}

// VideoOutput represents a generated video variant.
type VideoOutput struct {
	Index           int     `json:"index"`
	ContentType     string  `json:"content_type"`
	DurationSeconds float64 `json:"duration_seconds"`
	Size            string  `json:"size,omitempty"`
	FileSizeBytes   int64   `json:"file_size_bytes,omitempty"`
	ProviderURL     string  `json:"-"`
	CacheKey        string  `json:"-"`
	Cached          bool    `json:"-"`
}

// VideoUsage represents video generation usage.
type VideoUsage struct {
	GenerationSeconds float64 `json:"generation_seconds"`
	Model             string  `json:"model"`
}

// VideoError represents a video generation error.
type VideoError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// IsTerminal returns true if the job is in a terminal state.
func (j *VideoJob) IsTerminal() bool {
	return j.Status == StatusCompleted || j.Status == StatusFailed || j.Status == StatusCancelled
}

// ToSubmitResponse returns the 202 receipt response.
func (j *VideoJob) ToSubmitResponse() map[string]interface{} {
	return map[string]interface{}{
		"id":         j.ID,
		"object":     "video",
		"status":     j.Status,
		"model":      j.Model,
		"prompt":     j.Prompt,
		"created_at": j.CreatedAt.Unix(),
		"expires_at": j.ExpiresAt.Unix(),
		"progress":   j.Progress,
	}
}

// ToStatusResponse returns the full job status.
func (j *VideoJob) ToStatusResponse() map[string]interface{} {
	resp := map[string]interface{}{
		"id":         j.ID,
		"object":     "video",
		"status":     j.Status,
		"model":      j.Model,
		"prompt":     j.Prompt,
		"progress":   j.Progress,
		"created_at": j.CreatedAt.Unix(),
		"expires_at": j.ExpiresAt.Unix(),
	}
	if j.StartedAt != nil {
		resp["started_at"] = j.StartedAt.Unix()
	}
	if j.CompletedAt != nil {
		resp["completed_at"] = j.CompletedAt.Unix()
	}
	if len(j.Videos) > 0 {
		resp["videos"] = j.Videos
	}
	if j.Error != nil {
		resp["error"] = j.Error
	}
	if j.Usage != nil {
		resp["usage"] = j.Usage
	}
	if j.Cost > 0 {
		resp["cost"] = j.Cost
	}
	if len(j.ClientMetadata) > 0 {
		resp["metadata"] = j.ClientMetadata
	}
	if j.RemixSourceID != "" {
		resp["remix_source"] = j.RemixSourceID
	}
	return resp
}
