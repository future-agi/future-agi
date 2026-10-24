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
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// AsyncMiddleware checks for x-agentcc-async header and routes to async processing.
// If the header is not set, returns false and the caller should proceed normally.
// If the header is set, queues the job and writes the response, returning true.
//
// provider and orgCfg are what routing resolved for rc. The job runs on them,
// through the same pipeline as the synchronous request (runNonStream), so it
// reaches only the providers that request could reach and is gated and
// billed the same way.
//
// jobTimeout is the timeout ChatCompletion resolved for the request before
// the request ceiling (requestTimeout, or requestTimeoutFor under a rollout
// candidate's config), on the model the request named before routing
// (call-order step 11): the same rule, and the same model, as the
// synchronous request's timeout, even when routing then renamed the model.
func (h *Handlers) AsyncMiddleware(w http.ResponseWriter, r *http.Request, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig, jobTimeout time.Duration) bool {
	if h.asyncWorker == nil {
		return false
	}

	if r.Header.Get("x-agentcc-async") != "true" {
		return false
	}

	if rc.Request == nil {
		return false
	}

	// Refused to a key limited to its org's providers, which is every org key.
	// The job runs on the provider routing admitted (asyncExecutor), but outside
	// the handler's call-order hooks (idempotency, run budget), which settle
	// when the handler returns, before the job runs.
	if h.orgProvidersOnly(r.Context(), rc) {
		models.WriteError(w, errDeferredRunUnavailable("async inference"))
		return true
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

	// The job's own timeout. ChatCompletion held rc's timeout_ms at
	// server.max_request_timeout, the ceiling on a synchronous response; a
	// job writes none, so it runs to the timeout its request asked for
	// (jobTimeout), held only at async.max_job_timeout
	// (async.Worker.jobTimeout). The job and its executor copy rc below, and
	// this 202 carries no x-agentcc-timeout-ms.
	rc.Metadata["timeout_ms"] = strconv.FormatInt(jobTimeout.Milliseconds(), 10)

	now := time.Now()
	job := &async.Job{
		ID:           async.GenerateJobID(),
		Status:       async.StatusQueued,
		OrgID:        orgID,
		EndpointType: rc.EndpointType,
		Request:      rc.Request,
		Metadata:     jobMetadata(rc.Metadata),
		Exec:         h.asyncExecutor(r.Context(), r.Header, rc, provider, orgCfg),
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
func (h *Handlers) asyncExecutor(reqCtx context.Context, header http.Header, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) async.Executor {
	jobRC := rc.CloneRequest()
	// Async results are JSON, and only a non-stream request runs the
	// post-plugins (cost, credits, logging) inside the pipeline.
	jobRC.IsStream = false
	admitted := middleware.WithAuthorizationOf(context.Background(), reqCtx)
	// The request's retry headers were validated before routing; the job
	// retries as the request would have.
	retries, _ := routing.ParseRetryOverrides(header)

	return func(ctx context.Context) (*async.Execution, error) {
		jobRC.StartTime = time.Now()
		jobCtx := routing.WithRetryOverrides(middleware.WithAuthorizationOf(ctx, admitted), retries)
		// A key rotation promoted since the job was queued: run on the
		// instance serving now (an org's own provider stays as captured).
		_, err := h.runNonStream(jobCtx, jobRC, h.registry.CurrentInstance(provider), orgCfg)
		// A job whose answer an output check withheld fails with that
		// guardrail's error, as its synchronous request would.
		err = pipelineErr(jobRC, err)
		if err == nil && jobRC.Response != nil {
			// The response checks writeChatResponse makes for a sync request.
			presentUsageCost(jobRC)
			if apiErr := h.checkToolCalls(jobRC, jobRC.Response); apiErr != nil {
				err = apiErr
				jobRC.Response = nil
			}
		}
		result := &async.Execution{
			Response:         jobRC.Response,
			Provider:         jobRC.Provider,
			CredentialSource: jobRC.CredentialSource,
			Metadata:         jobMetadata(jobRC.Metadata),
		}
		jobRC.Release() // runs the finalizers the job's pre-plugins registered
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
// routing.model_timeouts entry, any providers.*.default_timeout, so a job
// always has at least the time its provider allows.
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
// caller's credentials (credentials.WithoutCredentials), so the job, which
// lives for its TTL and a day after, never holds the caller's key or an
// entry whose name reads as a credential. The worker reads timeout_ms from
// it. The executor's own copy of the request (asyncExecutor) keeps the
// Authorization header until the job runs: the job's pipeline authenticates
// the key again.
func jobMetadata(m map[string]string) map[string]string {
	return credentials.WithoutCredentials(m)
}
