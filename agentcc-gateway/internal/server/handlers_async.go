package server

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/credentials"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// AsyncMiddleware checks for x-agentcc-async header and routes to async processing.
// If the header is not set, returns false and the caller should proceed normally.
// If the header is set, queues the job and writes the response, returning true.
//
// provider and orgCfg are what routing resolved for rc. The job runs on them,
// through the same pipeline as the synchronous request (runNonStream), so it
// reaches only the providers that request can reach and is gated and
// billed the same way.
func (h *Handlers) AsyncMiddleware(w http.ResponseWriter, r *http.Request, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) bool {
	if h.asyncWorker == nil {
		return false
	}

	if r.Header.Get("x-agentcc-async") != "true" {
		return false
	}

	if rc.Request == nil {
		return false
	}

	// A managed-service (license) token may call only POST
	// /v1/chat/completions and cannot read job results, so its async request
	// is refused here, before any provider is called or the request metered.
	if middleware.IsLicenseAuthorized(r.Context()) {
		models.WriteError(w, models.ErrBadRequest("async_not_supported",
			"Async inference (x-agentcc-async) is not available to a managed service token, which cannot read job results; send the request without x-agentcc-async."))
		return true
	}

	// Parse TTL from header.
	ttl := async.DefaultResultTTL
	if v := r.Header.Get("x-agentcc-async-job-result-ttl"); v != "" {
		if secs, err := strconv.Atoi(v); err == nil && secs > 0 {
			ttl = time.Duration(secs) * time.Second
		}
	}

	// The job belongs to the org of the key KeyAuth authenticated, the same
	// org GetAsyncJob and DeleteAsyncJob check.
	orgID, ok := h.callerOrgID(w, r)
	if !ok {
		return true
	}

	now := time.Now()
	job := &async.Job{
		ID:           async.GenerateJobID(),
		Status:       async.StatusQueued,
		OrgID:        orgID,
		EndpointType: rc.EndpointType,
		Request:      rc.Request,
		Metadata:     jobMetadata(rc.Metadata),
		Exec:         h.asyncExecutor(r.Context(), rc, provider, orgCfg),
		CreatedAt:    now,
		TTL:          ttl,
		ExpiresAt:    now.Add(ttl + 24*time.Hour), // extra buffer before GC
	}

	// Once submitted, the job belongs to the store and a worker may already be
	// updating it, so read its state back through a locked snapshot. A job
	// the queue refuses is not stored: the request gets the reason.
	jobID, createdAt := job.ID, job.CreatedAt
	status, expiresAt := job.Status, job.ExpiresAt
	if apiErr := h.asyncWorker.Submit(job); apiErr != nil {
		models.WriteError(w, apiErr)
		return true
	}
	if h.asyncStore != nil {
		if snap := h.asyncStore.Get(jobID); snap != nil {
			status, expiresAt = snap.Status, snap.ExpiresAt
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"job_id":     jobID,
		"status":     status,
		"created_at": createdAt,
		"expires_at": expiresAt,
	})

	return true
}

// asyncExecutor returns the job executor for an async request: it runs a copy
// of rc, taken now because the handler releases rc when it returns, on
// provider through runNonStream, under the key or license authorization that
// admitted the request. It never resolves a provider itself.
func (h *Handlers) asyncExecutor(reqCtx context.Context, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) async.Executor {
	jobRC := rc.CloneRequest()
	// Async results are JSON, and only a non-stream request runs the
	// post-plugins (cost, credits, logging) inside the pipeline.
	jobRC.IsStream = false
	admitted := middleware.WithAuthorizationOf(context.Background(), reqCtx)

	return func(ctx context.Context) (*async.Execution, error) {
		jobRC.StartTime = time.Now()
		_, err := h.runNonStream(middleware.WithAuthorizationOf(ctx, admitted), jobRC, provider, orgCfg)
		result := &async.Execution{
			Response:         jobRC.Response,
			Provider:         jobRC.Provider,
			CredentialSource: jobRC.CredentialSource,
			Metadata:         jobMetadata(jobRC.Metadata),
		}
		jobRC.Release()
		return result, err
	}
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

	orgID, ok := h.callerOrgID(w, r)
	if !ok {
		return
	}
	// Another org's job answers 404, the same as a missing one.
	job := h.asyncStore.GetForOrg(jobID, orgID)
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

	orgID, ok := h.callerOrgID(w, r)
	if !ok {
		return
	}
	// Delete the job and, if it is running, cancel its provider call. Another
	// org's job answers 404, the same as a missing one, and is left
	// untouched.
	if !h.asyncStore.DeleteForOrg(jobID, orgID) {
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

// asyncWorkerConfig reads the async: section of cfg. A job's org chooses
// the provider instance it runs on, and the caller its timeout header, so
// only the operator's config may bound how long a job holds a worker that
// every org shares. An unset max_job_timeout is the longest timeout cfg gives
// a request or a provider: server.default_request_timeout, any
// routing.model_timeouts entry, any providers.*.default_timeout, so no job
// is cut shorter than one of those allows.
func asyncWorkerConfig(cfg *config.Config) async.WorkerConfig {
	maxJobTimeout := cfg.Async.MaxJobTimeout
	if maxJobTimeout <= 0 {
		maxJobTimeout = cfg.Server.DefaultRequestTimeout
		for _, d := range cfg.Routing.ModelTimeouts {
			maxJobTimeout = max(maxJobTimeout, d)
		}
		for _, p := range cfg.Providers {
			maxJobTimeout = max(maxJobTimeout, p.DefaultTimeout)
		}
	}
	return async.WorkerConfig{
		MaxWorkers:       cfg.Async.MaxWorkers,
		MaxWorkersPerOrg: cfg.Async.MaxWorkersPerOrg,
		MaxJobTimeout:    maxJobTimeout,
	}
}

// jobMetadata is the copy of a request's metadata an async job keeps, and
// the copy of its run's metadata it adds when the job finishes: without the
// caller's credentials (credentials.WithoutCredentials), so the metadata a
// stored job holds never contains the caller's key or an
// entry whose name reads as a credential. The worker reads timeout_ms from
// it. The executor's own copy of the request (asyncExecutor) keeps the
// Authorization header until the job runs: the job's pipeline authenticates
// the key again.
func jobMetadata(m map[string]string) map[string]string {
	return credentials.WithoutCredentials(m)
}
