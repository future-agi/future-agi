package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/otel"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/lifecycle"
)

func videoJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func videoError(w http.ResponseWriter, err error) {
	e := lifecycle.APIError(err)
	if e.Status == 429 && w.Header().Get("Retry-After") == "" {
		w.Header().Set("Retry-After", "1")
	}
	models.WriteError(w, e)
}
func (h *Handlers) videoReady(w http.ResponseWriter) bool {
	if h.videoStore == nil {
		models.WriteError(w, &models.APIError{Status: 501, Type: models.ErrTypeServer, Code: "not_configured", Message: "Video generation is not configured"})
		return false
	}
	if h.videoService == nil {
		videoError(w, video.ErrStoreUnavailable)
		return false
	}
	return true
}

// videoIdentity authenticates even when the general gateway permits anonymous
// chat. Video ownership is always derived from a live API key, never headers or
// body metadata supplied by the caller.
func (h *Handlers) videoIdentity(r *http.Request, rc *models.RequestContext) error {
	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = otel.NewSpan("video.request", "agentcc-gateway").TraceID
	rc.EndpointType = "video"
	rc.Metadata["cache_control"] = "no-store"
	setAuthMetadataFromRequest(rc, r)
	host, _, e := net.SplitHostPort(r.RemoteAddr)
	if e != nil {
		host = r.RemoteAddr
	}
	rc.Metadata["client_ip"] = host
	if h.keyStore == nil {
		return &models.APIError{Status: 403, Type: models.ErrTypeInvalidRequest, Code: "missing_org", Message: "Video generation requires an organization API key"}
	}
	result := authplugin.New(h.keyStore, true).ProcessRequest(r.Context(), rc)
	if result.Error != nil {
		return result.Error
	}
	org := rc.Metadata[tenant.MetadataKeyOrgID]
	if org == "" {
		return &models.APIError{Status: 403, Type: models.ErrTypeInvalidRequest, Code: "missing_org", Message: "Video generation requires an organization API key"}
	}
	rc.Metadata["org_id"] = org
	return nil
}
func (h *Handlers) SubmitVideo(w http.ResponseWriter, r *http.Request) {
	if !h.videoReady(w) {
		return
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	if e := h.videoIdentity(r, rc); e != nil {
		videoError(w, e)
		return
	}
	body, e := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if e != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_request", "Could not read video request"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{Status: 413, Type: models.ErrTypeInvalidRequest, Code: "request_too_large", Message: "Request body exceeds maximum size"})
		return
	}
	req, e := capability.Decode(body)
	if e != nil {
		videoError(w, e)
		return
	}
	if h.tenantStore != nil {
		org := h.tenantStore.Get(rc.Metadata["org_id"])
		h.applyOrgRateLimitOverrides(org, rc)
		h.applyOrgBudgetOverrides(org, rc)
		h.applyOrgIPACLOverrides(org, rc)
	}
	rc.Model = req.Model
	rc.Provider, _, _ = strings.Cut(req.Model, "/")
	// No request body, client metadata or raw headers are attached to the logging
	// context; the canonical request belongs only in the retained job record.
	var accepted video.AcceptResult
	call := func(ctx context.Context, rc *models.RequestContext) error {
		var scopes []video.BudgetReservation
		if h.tenantStore != nil {
			if org := h.tenantStore.Get(rc.Metadata["org_id"]); org != nil {
				scopes = budgetplugin.HierarchyScopes(org.Budgets, rc)
			}
		}
		var err error
		accepted, err = h.videoService.Accept(ctx, rc, req, r.Header.Get("Idempotency-Key"), scopes)
		if err != nil {
			return lifecycle.APIError(err)
		}
		return nil
	}
	if h.engine == nil {
		videoError(w, video.ErrStoreUnavailable)
		return
	}
	if e = h.engine.Process(r.Context(), rc, call); e != nil {
		apiErr := lifecycle.APIError(e)
		if apiErr.Status == 403 && strings.Contains(apiErr.Message, "model") {
			apiErr = &models.APIError{Status: 403, Type: models.ErrTypeInvalidRequest, Code: "model_forbidden", Message: "API key cannot access this model"}
		}
		if retry := rc.Metadata["ratelimit_reset"]; retry != "" {
			w.Header().Set("Retry-After", retry)
		}
		videoError(w, apiErr)
		return
	}
	if accepted.Job == nil {
		videoError(w, video.ErrStoreUnavailable)
		return
	}
	job := accepted.Job
	if h.videoSyncWait > 0 && job.Status == video.StatusSubmitting && job.Phase == video.PhasePrepared {
		timer := time.NewTimer(h.videoSyncWait)
		ticker := time.NewTicker(250 * time.Millisecond)
		defer timer.Stop()
		defer ticker.Stop()
	wait:
		for {
			select {
			case <-r.Context().Done():
				return
			case <-timer.C:
				break wait
			case <-ticker.C:
				j, err := h.videoService.Get(r.Context(), job.ID, job.OrgID)
				if err != nil {
					videoError(w, err)
					return
				}
				job = j
				if job.Phase != video.PhasePrepared || job.Status != video.StatusSubmitting {
					break wait
				}
			}
		}
	}
	h.setAgentccHeaders(w, rc)
	status := 202
	if accepted.Replay && job.IsTerminal() {
		status = 200
	}
	videoJSON(w, status, job.ToSubmitResponse())
}
func (h *Handlers) videoReadContext(w http.ResponseWriter, r *http.Request) *models.RequestContext {
	if !h.videoReady(w) {
		return nil
	}
	rc := models.AcquireRequestContext()
	if err := h.videoIdentity(r, rc); err != nil {
		rc.Release()
		videoError(w, err)
		return nil
	}
	return rc
}
func (h *Handlers) GetVideoStatus(w http.ResponseWriter, r *http.Request) {
	rc := h.videoReadContext(w, r)
	if rc == nil {
		return
	}
	defer rc.Release()
	j, e := h.videoService.Get(r.Context(), r.URL.Query().Get("video_id"), rc.Metadata["org_id"])
	if e != nil {
		videoError(w, e)
		return
	}
	videoJSON(w, 200, j.ToStatusResponse())
}
func (h *Handlers) ListVideos(w http.ResponseWriter, r *http.Request) {
	rc := h.videoReadContext(w, r)
	if rc == nil {
		return
	}
	defer rc.Release()
	q := r.URL.Query()
	f := video.VideoListFilters{Limit: 20, Order: "desc", Status: q.Get("status"), Model: q.Get("model")}
	var err error
	for _, field := range []string{"limit", "offset"} {
		if q.Has(field) {
			n, e := strconv.Atoi(q.Get(field))
			if e != nil {
				err = e
				break
			}
			if field == "limit" {
				f.Limit = n
			} else {
				f.Offset = n
			}
		}
	}
	if q.Has("order") {
		f.Order = q.Get("order")
	}
	if err != nil || q.Has("order") && f.Order == "" {
		models.WriteError(w, models.ErrBadRequest("invalid_filter", "Invalid video list filter"))
		return
	}
	jobs, total, e := h.videoService.List(rc.Metadata["org_id"], f)
	if e != nil {
		videoError(w, e)
		return
	}
	rows := make([]map[string]any, 0, len(jobs))
	for _, j := range jobs {
		rows = append(rows, j.ToStatusResponse())
	}
	videoJSON(w, 200, map[string]any{"object": "list", "data": rows, "total": total, "limit": f.Limit, "offset": f.Offset, "has_more": f.Offset+len(rows) < total})
}
func (h *Handlers) CancelVideo(w http.ResponseWriter, r *http.Request) {
	rc := h.videoReadContext(w, r)
	if rc == nil {
		return
	}
	defer rc.Release()
	j, e := h.videoService.Cancel(r.Context(), r.URL.Query().Get("video_id"), rc.Metadata["org_id"])
	if e != nil {
		videoError(w, e)
		return
	}
	status := 200
	if j.CancelState == "requested" {
		status = 202
	}
	videoJSON(w, status, j.ToStatusResponse())
}
func (h *Handlers) DeleteVideo(w http.ResponseWriter, r *http.Request) {
	rc := h.videoReadContext(w, r)
	if rc == nil {
		return
	}
	defer rc.Release()
	j, e := h.videoService.Delete(r.Context(), r.URL.Query().Get("video_id"), rc.Metadata["org_id"])
	if e != nil {
		videoError(w, e)
		return
	}
	videoJSON(w, 200, map[string]any{"id": j.ID, "object": "video", "deleted": true, "deletion_scope": "local_only", "upstream_may_continue": j.UpstreamMayContinue, "accounting_retained": true})
}
func (h *Handlers) GetVideoContent(w http.ResponseWriter, r *http.Request) {
	rc := h.videoReadContext(w, r)
	if rc == nil {
		return
	}
	defer rc.Release()
	index := 0
	var e error
	if r.URL.Query().Has("artifact_index") {
		index, e = strconv.Atoi(r.URL.Query().Get("artifact_index"))
	}
	if e != nil || index < 0 {
		models.WriteError(w, models.ErrBadRequest("invalid_artifact_index", "Invalid artifact index"))
		return
	}
	id := r.URL.Query().Get("video_id")
	obj, e := h.videoService.Content(r.Context(), id, rc.Metadata["org_id"], index, r.Header.Get("Range"))
	if e != nil {
		if lifecycle.APIError(e).Status == 416 {
			w.Header().Set("Content-Range", fmt.Sprintf("bytes */%d", obj.Meta.Bytes))
		}
		videoError(w, e)
		return
	}
	defer obj.Body.Close()
	w.Header().Set("Content-Type", obj.Meta.ContentType)
	w.Header().Set("Content-Length", strconv.FormatInt(obj.Length, 10))
	w.Header().Set("Accept-Ranges", "bytes")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", fmt.Sprintf("%s_%d.mp4", id, index)))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	status := 200
	if obj.ContentRange != "" {
		status = 206
		w.Header().Set("Content-Range", obj.ContentRange)
	}
	w.WriteHeader(status)
	_, _ = io.Copy(w, obj.Body)
}
