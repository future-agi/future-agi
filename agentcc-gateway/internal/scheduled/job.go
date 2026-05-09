package scheduled

import (
	"encoding/json"
	"time"
)

// Job status constants.
const (
	StatusPending   = "pending"
	StatusRunning   = "running"
	StatusCompleted = "completed"
	StatusFailed    = "failed"
	StatusCancelled = "cancelled"
	StatusExpired   = "expired"
)

// ScheduledJob represents a completion request scheduled for future execution.
type ScheduledJob struct {
	ID          string            `json:"id"`
	OrgID       string            `json:"org_id,omitempty"`
	Status      string            `json:"status"`
	ScheduledAt time.Time         `json:"scheduled_at"`
	CreatedAt   time.Time         `json:"created_at"`
	UpdatedAt   time.Time         `json:"updated_at"`
	Request     json.RawMessage   `json:"request"`
	Model       string            `json:"model"`
	Response    json.RawMessage   `json:"response,omitempty"`
	Error       string            `json:"error,omitempty"`
	Attempts    int               `json:"attempts"`
	MaxAttempts int               `json:"max_attempts"`
	WebhookURL  string            `json:"webhook_url,omitempty"`
	Metadata    map[string]string `json:"metadata,omitempty"`
	ExpiresAt   time.Time         `json:"expires_at,omitempty"`
}

// IsTerminal returns true if the job is in a final state.
func (j *ScheduledJob) IsTerminal() bool {
	switch j.Status {
	case StatusCompleted, StatusFailed, StatusCancelled, StatusExpired:
		return true
	}
	return false
}

// CanCancel returns true if the job can be cancelled.
func (j *ScheduledJob) CanCancel() bool {
	return j.Status == StatusPending
}
