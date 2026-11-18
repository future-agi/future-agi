package server

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	ipaclplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
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

	caller, ok := h.asyncCaller(r)
	if !ok {
		models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
		return true
	}

	// Parse TTL from header.
	ttl := async.DefaultResultTTL
	if v := r.Header.Get("x-agentcc-async-job-result-ttl"); v != "" {
		if secs, err := strconv.Atoi(v); err == nil && secs > 0 {
			ttl = time.Duration(secs) * time.Second
		}
	}

	// Determine org ID for scoping: the authenticated key's organization when
	// keys are in use, otherwise the request's resolved organization.
	orgID := caller.orgID
	if caller.rawKey == "" {
		orgID = rc.Metadata["org_id"]
	}

	now := time.Now()
	job := &async.Job{
		ID:           async.GenerateJobID(),
		Status:       async.StatusQueued,
		OrgID:        orgID,
		Principal:    caller.principal,
		EndpointType: rc.EndpointType,
		Request:      rc.Request,
		Metadata:     copyMetadata(rc.Metadata),
		CreatedAt:    now,
		TTL:          ttl,
		ExpiresAt:    now.Add(ttl + 24*time.Hour), // extra buffer before GC
	}

	// Capture the admission response before a worker can mutate the live job.
	response := map[string]interface{}{
		"job_id":     job.ID,
		"status":     job.Status,
		"created_at": job.CreatedAt,
		"expires_at": job.ExpiresAt,
	}
	h.asyncWorker.Submit(job)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(response)

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

	caller, ok := h.asyncCaller(r)
	if !ok {
		models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
		return
	}
	if err := h.resultNetworkAdmission(r, &caller); err != nil {
		models.WriteError(w, err)
		return
	}
	job := h.asyncStore.GetForPrincipal(jobID, caller.principal)

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

	caller, ok := h.asyncCaller(r)
	if !ok {
		models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
		return
	}
	if err := h.resultNetworkAdmission(r, &caller); err != nil {
		models.WriteError(w, err)
		return
	}

	// Authorize and cancel atomically; never authorize a snapshot then delete by ID.
	if !h.asyncStore.CancelForPrincipal(jobID, caller.principal) {
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

// asyncCallerInfo identifies who submits or accesses an async job.
type asyncCallerInfo struct {
	// principal owns the job. It is empty only when key auth is disabled.
	principal string
	// orgID is the authenticated key's organization ("" for keys without one).
	orgID string
	// rawKey is the key the caller authenticated with ("" without key auth).
	rawKey string
}

// asyncCaller uses the same credential precedence as KeyAuth. Org-scoped
// keys share their organization's jobs; keys without an org own only their jobs.
func (h *Handlers) asyncCaller(r *http.Request) (asyncCallerInfo, bool) {
	// A license token authorizes synchronous requests only; it is not an owner
	// for stored async jobs.
	if middleware.IsLicenseAuthorized(r.Context()) {
		return asyncCallerInfo{}, false
	}
	if h.keyStore == nil {
		if h.asyncKeyRequired {
			return asyncCallerInfo{}, false
		}
		return asyncCallerInfo{}, true // auth explicitly disabled
	}
	rawKey := r.Header.Get("x-api-key")
	if header := r.Header.Get("Authorization"); strings.HasPrefix(header, "Bearer ") {
		rawKey = strings.TrimPrefix(header, "Bearer ")
	}
	key := h.keyStore.Authenticate(rawKey)
	if key == nil {
		return asyncCallerInfo{}, false
	}
	caller := asyncCallerInfo{orgID: key.Metadata["org_id"], rawKey: rawKey}
	if caller.orgID != "" {
		caller.principal = "org:" + caller.orgID
	} else {
		caller.principal = "key:" + key.ID
	}
	return caller, true
}

// resultNetworkAdmission applies the network rules that guard inference to a
// read or cancellation of a stored result, without calling a provider or
// charging for it. A nil caller (administrator routes) is checked against the
// global rules only. With a key, the key's own IP allowlist and its
// organization's rules apply too; the organization comes from the
// authenticated key, never from request metadata. Rules are read on every call
// so a changed organization configuration takes effect immediately.
func (h *Handlers) resultNetworkAdmission(r *http.Request, caller *asyncCallerInfo) *models.APIError {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	// Only the configured number of trusted proxy hops is read from
	// X-Forwarded-For, as for video routes.
	rc.Metadata["client_ip"] = trustedClientIP(r, h.resultIPACL.TrustedProxies)
	if caller != nil && caller.rawKey != "" {
		rc.Metadata["authorization"] = "Bearer " + caller.rawKey
		if result := authplugin.New(h.keyStore, true).ProcessRequest(r.Context(), rc); result.Error != nil {
			return result.Error
		}
		rc.Metadata[tenant.MetadataKeyOrgID] = caller.orgID
	}
	return ipaclplugin.New(h.resultIPACL, h.tenantStore).ProcessRequest(r.Context(), rc).Error
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
