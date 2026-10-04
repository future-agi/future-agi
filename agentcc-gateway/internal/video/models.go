package video

import (
	"encoding/json"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"time"
)

// VideoJob represents a video generation job.
type VideoJob struct {
	KeyID                string              `json:"-" store:"key_id"`
	Creator              string              `json:"-" store:"creator"`
	Phase                string              `json:"-" store:"phase"`
	ProviderState        string              `json:"provider_state,omitempty"`
	Service              string              `json:"-" store:"service"`
	ModelID              string              `json:"-" store:"model_id"`
	Region               string              `json:"-" store:"region"`
	AccountRef           string              `json:"-" store:"account_ref"`
	CapabilityRevision   string              `json:"-" store:"capability_revision"`
	RequestCanonical     json.RawMessage     `json:"-" store:"request_canonical"`
	Fingerprint          string              `json:"-" store:"fingerprint"`
	IdemDigest           string              `json:"-" store:"idem_digest"`
	IdemOp               string              `json:"-" store:"idem_op"`
	ProviderRequestID    string              `json:"-" store:"provider_request_id"`
	CorrelationToken     string              `json:"-" store:"correlation_token"`
	AttemptID            string              `json:"-" store:"attempt_id"`
	AttemptStartedAt     time.Time           `json:"-" store:"attempt_started_at"`
	LeaseOwner           string              `json:"-" store:"lease_owner"`
	LeaseFence           int64               `json:"-" store:"lease_fence"`
	UpdatedAt            time.Time           `json:"updated_at"`
	SubmittedAt          *time.Time          `json:"submitted_at,omitempty"`
	LastCheckedAt        *time.Time          `json:"last_checked_at,omitempty"`
	LateSuccessAt        *time.Time          `json:"late_success_at,omitempty"`
	SubmitBy             time.Time           `json:"-" store:"submit_by"`
	RunBy                time.Time           `json:"-" store:"run_by"`
	ReconcileBy          time.Time           `json:"-" store:"reconcile_by"`
	NextPollAt           time.Time           `json:"-" store:"next_poll_at"`
	PollIntervalMS       int64               `json:"-" store:"poll_interval_ms"`
	CancelState          string              `json:"-" store:"cancel_state"`
	CancelRequestedAt    *time.Time          `json:"-" store:"cancel_requested_at"`
	ReconcileState       string              `json:"-" store:"reconcile_state"`
	ReconcileReason      string              `json:"-" store:"reconcile_reason"`
	UpstreamMayContinue  bool                `json:"upstream_may_continue"`
	RetrySafe            bool                `json:"retry_safe"`
	EstimateUnit         provider.Unit       `json:"-" store:"estimate_unit"`
	EstimateQty          float64             `json:"-" store:"estimate_qty"`
	EstimateUSD          float64             `json:"-" store:"estimate_usd"`
	ReservedMicros       int64               `json:"-" store:"reserved_micros"`
	SettledMicros        int64               `json:"-" store:"settled_micros"`
	TariffRevision       string              `json:"-" store:"tariff_revision"`
	SettlementState      string              `json:"-" store:"settlement_state"`
	Reservations         []BudgetReservation `json:"-" store:"reservations_json"`
	Artifacts            []Artifact          `json:"-" store:"artifacts_json"`
	DeletedAt            *time.Time          `json:"-" store:"deleted_at"`
	IdempotencyExpiresAt time.Time           `json:"-" store:"idempotency_expires_at"`
	EndUserHash          string              `json:"-" store:"end_user_hash"`
	TraceID              string              `json:"-" store:"trace_id"`

	ID             string            `json:"id"`
	OrgID          string            `json:"org_id,omitempty"`
	Status         string            `json:"status"`
	Model          string            `json:"model"`
	Provider       string            `json:"provider,omitempty"`
	ProviderJobID  string            `json:"-" store:"provider_job_id"`
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
	Usage          *VideoUsage       `json:"usage,omitempty" store:"usage_json"`
	Cost           float64           `json:"cost,omitempty"`
	ClientMetadata map[string]string `json:"metadata,omitempty" store:"metadata_json"`
	RemixSourceID  string            `json:"remix_source,omitempty"`
	CreatedAt      time.Time         `json:"created_at"`
	StartedAt      *time.Time        `json:"started_at,omitempty"`
	CompletedAt    *time.Time        `json:"completed_at,omitempty"`
	ExpiresAt      time.Time         `json:"expires_at"`
	PollFailures   int               `json:"-" store:"poll_failures"`
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
	Lines             []provider.UsageLine `json:"lines,omitempty"`
	GenerationSeconds float64              `json:"generation_seconds"`
	Model             string               `json:"model"`
}

// VideoError represents a video generation error.
type VideoError struct {
	ProviderRequestID string `json:"provider_request_id,omitempty"`
	Retryable         bool   `json:"retryable"`
	Code              string `json:"code"`
	Message           string `json:"message"`
}

// IsTerminal returns true if the job is in a terminal state.
func (j *VideoJob) IsTerminal() bool {
	return IsTerminal(j.Status)
}

// ToSubmitResponse returns the 202 receipt response.
func (j *VideoJob) ToSubmitResponse() map[string]interface{} {
	return map[string]interface{}{
		"id":         j.ID,
		"object":     "video",
		"status":     j.Status,
		"model":      j.Model,
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

// Artifact is internal persistence data. Public responses omit BlobKey.
type Artifact struct {
	Index           int         `json:"index"`
	ContentType     string      `json:"content_type"`
	Bytes           int64       `json:"bytes"`
	Width           int         `json:"width"`
	Height          int         `json:"height"`
	DurationSeconds float64     `json:"duration_seconds"`
	BlobKey         string      `json:"blob_key,omitempty"`
	State           string      `json:"state"`
	ExpiresAt       time.Time   `json:"expires_at"`
	Error           *VideoError `json:"error,omitempty"`
}
