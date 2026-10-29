package server

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
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

	// Owner comes from the same key lookup GET and DELETE use. The auth plugin
	// writes auth_key_id only inside engine.Process, which runs after this
	// middleware, so that field is not available here.
	orgID := ""
	if h.keyStore != nil {
		scope, scoped := h.asyncCallerScope(r)
		if scoped && scope != "" && scope[0] != '\x00' {
			orgID = scope
		}
	} else if v, ok := rc.Metadata["org_id"]; ok {
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

	scope, scoped := h.asyncCallerScope(r)
	var job *async.Job
	if scoped {
		job = h.asyncStore.GetForOrg(jobID, scope)
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

	// Cancel the live job only after the caller scope matches. Get returns a
	// snapshot with CancelFn stripped, so a snapshot cancel does not stop work.
	// Auth disabled is single-tenant: cancel by the stored owner, including empty.
	scope, scoped := h.asyncCallerScope(r)
	var cancelled bool
	if scoped {
		cancelled = h.asyncStore.CancelForOrg(jobID, scope)
	} else {
		cancelled = h.asyncStore.CancelUnscoped(jobID)
	}
	if !cancelled {
		models.WriteError(w, models.ErrNotFound("job_not_found", "Async job not found"))
		return
	}

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

// asyncCallerScope resolves the authenticated caller for async job access.
// A configured key store means auth is enabled: scope is the key's org_id,
// or the key id when the key has no org. A missing key is scoped to a value
// that matches nothing, so the caller cannot fall through to a global read.
// A nil key store is auth-disabled and stays single-tenant.
func (h *Handlers) asyncCallerScope(r *http.Request) (scope string, scoped bool) {
	if h.keyStore == nil {
		return "", false
	}
	rawKey := ""
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		rawKey = strings.TrimPrefix(authHeader, "Bearer ")
	}
	if rawKey == "" {
		rawKey = r.Header.Get("x-api-key")
	}
	apiKey := h.keyStore.Lookup(rawKey)
	if apiKey == nil {
		return "\x00unauthenticated", true
	}
	if orgID := apiKey.Metadata["org_id"]; orgID != "" {
		return orgID, true
	}
	if apiKey.ID == "" {
		return "\x00unscoped-key", true
	}
	return apiKey.ID, true
}
