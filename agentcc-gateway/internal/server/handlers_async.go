package server

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AsyncMiddleware checks for x-agentcc-async header and routes to async processing.
// If the header is not set, returns false and the caller should proceed normally.
// If the header is set, queues the job and writes the response, returning true.
func (h *Handlers) AsyncMiddleware(w http.ResponseWriter, r *http.Request, rc *models.RequestContext) bool {
	if h.asyncWorker == nil {
		return false
	}

	if r.Header.Get("x-agentcc-async") != "true" {
		return false
	}

	if rc.Request == nil {
		return false
	}

	// Parse TTL from header.
	ttl := async.DefaultResultTTL
	if v := r.Header.Get("x-agentcc-async-job-result-ttl"); v != "" {
		if secs, err := strconv.Atoi(v); err == nil && secs > 0 {
			ttl = time.Duration(secs) * time.Second
		}
	}

	// Determine org ID for scoping.
	orgID := ""
	if v, ok := rc.Metadata["org_id"]; ok {
		orgID = v
	}

	now := time.Now()
	job := &async.Job{
		ID:           async.GenerateJobID(),
		Status:       async.StatusQueued,
		OrgID:        orgID,
		EndpointType: rc.EndpointType,
		Request:      rc.Request,
		Metadata:     copyMetadata(rc.Metadata),
		CreatedAt:    now,
		TTL:          ttl,
		ExpiresAt:    now.Add(ttl + 24*time.Hour), // extra buffer before GC
	}

	h.asyncWorker.Submit(job)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"job_id":     job.ID,
		"status":     job.Status,
		"created_at": job.CreatedAt,
		"expires_at": job.ExpiresAt,
	})

	return true
}

// GetAsyncJob handles GET /v1/async/{job_id}.
func (h *Handlers) GetAsyncJob(w http.ResponseWriter, r *http.Request) {
	if h.asyncStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Async inference is not configured",
		})
		return
	}

	jobID := r.URL.Query().Get("job_id")
	if jobID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_job_id", "job_id is required"))
		return
	}

	// Extract org ID from auth for scoping.
	orgID := ""
	rc := models.AcquireRequestContext()
	if authHeader := r.Header.Get("Authorization"); authHeader != "" {
		rc.Metadata["authorization"] = authHeader
	}
	if v, ok := rc.Metadata["org_id"]; ok {
		orgID = v
	}
	rc.Release()

	var job *async.Job
	if orgID != "" {
		job = h.asyncStore.GetForOrg(jobID, orgID)
	} else {
		job = h.asyncStore.Get(jobID)
	}

	if job == nil {
		models.WriteError(w, models.ErrNotFound("job_not_found", "Async job not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(job.ToResponse())
}

// DeleteAsyncJob handles DELETE /v1/async/{job_id}.
func (h *Handlers) DeleteAsyncJob(w http.ResponseWriter, r *http.Request) {
	if h.asyncStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Async inference is not configured",
		})
		return
	}

	jobID := r.URL.Query().Get("job_id")
	if jobID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_job_id", "job_id is required"))
		return
	}

	// Try to cancel a running job.
	job := h.asyncStore.Get(jobID)
	if job == nil {
		models.WriteError(w, models.ErrNotFound("job_not_found", "Async job not found"))
		return
	}

	// Cancel if still running.
	if job.Status == async.StatusQueued || job.Status == async.StatusProcessing {
		now := time.Now()
		job.Status = async.StatusCancelled
		job.CompletedAt = &now
		if job.CancelFn != nil {
			job.CancelFn()
		}
	}

	// Delete from store.
	h.asyncStore.Delete(jobID)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"job_id":  jobID,
		"status":  "cancelled",
		"deleted": true,
	})
}

func copyMetadata(m map[string]string) map[string]string {
	if m == nil {
		return nil
	}
	cp := make(map[string]string, len(m))
	for k, v := range m {
		cp[k] = v
	}
	return cp
}
