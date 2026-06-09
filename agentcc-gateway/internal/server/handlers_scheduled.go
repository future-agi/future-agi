package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
)

// scheduledSubmitRequest is the API request body for submitting a scheduled job.
type scheduledSubmitRequest struct {
	ScheduledAt string                          `json:"scheduled_at"` // RFC3339
	Delay       string                          `json:"delay"`        // Go duration string
	Request     json.RawMessage                 `json:"request"`
	WebhookURL  string                          `json:"webhook_url"`
	Metadata    map[string]string               `json:"metadata"`
}

// SubmitScheduled handles POST /v1/scheduled.
func (h *Handlers) SubmitScheduled(w http.ResponseWriter, r *http.Request) {
	if h.scheduledStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_enabled",
			Message: "Scheduled completions are not enabled",
		})
		return
	}

	var req scheduledSubmitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}

	if len(req.Request) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_request", "request field is required"))
		return
	}

	// Parse scheduled time.
	var scheduledAt time.Time
	if req.ScheduledAt != "" {
		var err error
		scheduledAt, err = time.Parse(time.RFC3339, req.ScheduledAt)
		if err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_time", "scheduled_at must be RFC3339 format: "+err.Error()))
			return
		}
	} else if req.Delay != "" {
		delay, err := time.ParseDuration(req.Delay)
		if err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_delay", "delay must be a Go duration string: "+err.Error()))
			return
		}
		if delay < 10*time.Second {
			models.WriteError(w, models.ErrBadRequest("delay_too_short", "minimum delay is 10s"))
			return
		}
		scheduledAt = time.Now().Add(delay)
	} else {
		models.WriteError(w, models.ErrBadRequest("missing_schedule", "either scheduled_at or delay is required"))
		return
	}

	if scheduledAt.Before(time.Now()) {
		models.WriteError(w, models.ErrBadRequest("past_time", "scheduled_at must be in the future"))
		return
	}

	maxAhead := h.scheduledMaxAhead
	if maxAhead <= 0 {
		maxAhead = 168 * time.Hour // 7 days
	}
	if scheduledAt.After(time.Now().Add(maxAhead)) {
		models.WriteError(w, models.ErrBadRequest("too_far_ahead",
			fmt.Sprintf("scheduled_at must be within %s", maxAhead)))
		return
	}

	// Extract model from the request for display.
	var partial struct {
		Model string `json:"model"`
	}
	json.Unmarshal(req.Request, &partial)

	requestID := models.GetRequestID(r.Context())
	orgID := "" // extracted from auth middleware if available

	job := &scheduled.ScheduledJob{
		ID:          "sched-" + requestID,
		OrgID:       orgID,
		Status:      scheduled.StatusPending,
		ScheduledAt: scheduledAt,
		CreatedAt:   time.Now(),
		UpdatedAt:   time.Now(),
		Request:     req.Request,
		Model:       partial.Model,
		MaxAttempts: h.scheduledRetryAttempts,
		WebhookURL:  req.WebhookURL,
		Metadata:    req.Metadata,
	}

	if err := h.scheduledStore.Create(job); err != nil {
		if err == scheduled.ErrOrgLimitExceeded {
			models.WriteError(w, &models.APIError{
				Status:  http.StatusTooManyRequests,
				Type:    "rate_limit_error",
				Code:    "too_many_jobs",
				Message: "Maximum pending scheduled jobs exceeded",
			})
			return
		}
		models.WriteError(w, models.ErrInternal("Failed to create scheduled job: "+err.Error()))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(job)
}

// GetScheduledJob handles GET /v1/scheduled/{job_id}.
func (h *Handlers) GetScheduledJob(w http.ResponseWriter, r *http.Request) {
	if h.scheduledStore == nil {
		models.WriteError(w, models.ErrNotFound("not_enabled", "Scheduled completions are not enabled"))
		return
	}

	jobID := extractPathParam(r.URL.Path, "/v1/scheduled/")
	if jobID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "job_id is required"))
		return
	}

	job, err := h.scheduledStore.Get(jobID)
	if err != nil {
		models.WriteError(w, models.ErrNotFound("job_not_found", "Scheduled job not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}

// ListScheduledJobs handles GET /v1/scheduled.
func (h *Handlers) ListScheduledJobs(w http.ResponseWriter, r *http.Request) {
	if h.scheduledStore == nil {
		models.WriteError(w, models.ErrNotFound("not_enabled", "Scheduled completions are not enabled"))
		return
	}

	status := r.URL.Query().Get("status")
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if l, err := strconv.Atoi(v); err == nil && l > 0 {
			limit = l
		}
	}
	if limit > 100 {
		limit = 100
	}

	orgID := "" // from auth
	jobs, err := h.scheduledStore.ListByOrg(orgID, status, limit)
	if err != nil {
		models.WriteError(w, models.ErrInternal("Failed to list jobs: "+err.Error()))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"data":   jobs,
	})
}

// CancelScheduledJob handles DELETE /v1/scheduled/{job_id}.
func (h *Handlers) CancelScheduledJob(w http.ResponseWriter, r *http.Request) {
	if h.scheduledStore == nil {
		models.WriteError(w, models.ErrNotFound("not_enabled", "Scheduled completions are not enabled"))
		return
	}

	jobID := extractPathParam(r.URL.Path, "/v1/scheduled/")
	if jobID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "job_id is required"))
		return
	}

	job, err := h.scheduledStore.Get(jobID)
	if err != nil {
		models.WriteError(w, models.ErrNotFound("job_not_found", "Scheduled job not found"))
		return
	}

	if !job.CanCancel() {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusConflict,
			Type:    "invalid_request_error",
			Code:    "job_not_cancellable",
			Message: fmt.Sprintf("Job is %s and cannot be cancelled", job.Status),
		})
		return
	}

	job.Status = scheduled.StatusCancelled
	if err := h.scheduledStore.Update(job); err != nil {
		models.WriteError(w, models.ErrInternal("Failed to cancel job: "+err.Error()))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(job)
}

// extractPathParam extracts the trailing parameter from a URL path.
func extractPathParam(path, prefix string) string {
	if !strings.HasPrefix(path, prefix) {
		return ""
	}
	param := strings.TrimPrefix(path, prefix)
	param = strings.TrimSuffix(param, "/")
	return param
}
