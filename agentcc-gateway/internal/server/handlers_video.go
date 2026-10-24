package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// errEndpointNotConfigured is the 501 endpoint_not_configured of an
// endpoint this gateway has nothing behind: the video routes
// with no video provider, and the scheduled routes.
func errEndpointNotConfigured(message string) *models.APIError {
	return &models.APIError{
		Status:  http.StatusNotImplemented,
		Type:    models.ErrTypeServer,
		Code:    "endpoint_not_configured",
		Message: message,
	}
}

// videoGate writes the 501 and reports false unless a video provider is
// registered and the job store is set. Every /v1/videos route calls it
// first, so an unconfigured gateway stores nothing.
func (h *Handlers) videoGate(w http.ResponseWriter) bool {
	if h.videoStore != nil && h.videoProviderConfigured() {
		return true
	}
	models.WriteError(w, errEndpointNotConfigured("Video generation is not configured on this gateway: no provider serves it"))
	return false
}

// videoProviderConfigured reports whether a registered provider implements
// providers.VideoProvider.
func (h *Handlers) videoProviderConfigured() bool {
	if h.registry == nil {
		return false
	}
	for _, id := range h.registry.ProviderIDs() {
		p, ok := h.registry.GetProvider(id)
		if !ok {
			continue
		}
		if _, ok := p.(providers.VideoProvider); ok {
			return true
		}
	}
	return false
}

// videoCaller returns the org and key id of the key KeyAuth authenticated,
// which scope every video job, as KeyAuth recorded them (KeyIdentity): a key
// updated or re-synced after KeyAuth cannot change the answer. With auth off
// there is one tenant, the no-org one. When auth is on and KeyAuth
// authenticated no key it writes 401 and reports false, so such a call never
// falls into the no-org tenant. A license token reaches only POST
// /v1/chat/completions, so it never gets here.
func (h *Handlers) videoCaller(w http.ResponseWriter, r *http.Request) (orgID, keyID string, ok bool) {
	if h.keyStore == nil {
		return "", "", true
	}
	if id, ok := middleware.KeyIdentity(r.Context()); ok {
		return id.OrgID, id.KeyID, true
	}
	models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
	return "", "", false
}

// errVideoNotFound answers an unknown job id and another org's alike.
func errVideoNotFound() *models.APIError {
	return models.ErrNotFound("video_not_found", "Video job not found")
}

// videoSubmitRequest is the client-facing video generation request.
type videoSubmitRequest struct {
	Model       string            `json:"model"`
	Prompt      string            `json:"prompt"`
	Duration    float64           `json:"duration,omitempty"`
	Size        string            `json:"size,omitempty"`
	FPS         int               `json:"fps,omitempty"`
	N           int               `json:"n,omitempty"`
	Style       string            `json:"style,omitempty"`
	AspectRatio string            `json:"aspect_ratio,omitempty"`
	Metadata    map[string]string `json:"metadata,omitempty"`
}

// SubmitVideo handles POST /v1/videos.
func (h *Handlers) SubmitVideo(w http.ResponseWriter, r *http.Request) {
	if !h.videoGate(w) {
		return
	}
	orgID, keyID, ok := h.videoCaller(w, r)
	if !ok {
		return
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "video"

	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req videoSubmitRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.Prompt == "" {
		models.WriteError(w, models.ErrBadRequest("missing_prompt", "prompt is required"))
		return
	}

	if req.N == 0 {
		req.N = 1
	}
	if req.N > 4 {
		models.WriteError(w, models.ErrBadRequest("invalid_n", "n must be between 1 and 4"))
		return
	}

	// The checks on the request read the prompt, as on every route that
	// sends one to a model, before the job is stored: the key is
	// authenticated as the pipeline authenticates it, X-Guardrail-Policy
	// applies, and a check's rewrite of the prompt is the prompt the job
	// keeps. A refused request stores nothing.
	setAuthMetadataFromRequest(rc, r)
	rc.Metadata["client_ip"] = middleware.ClientIP(r)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	readGuardrailPolicyHeader(rc, r)
	rc.PassThrough = requestview.VideoPrompt(req.Model, &req.Prompt)
	if apiErr := h.engine.RunRequestPlugins(r.Context(), rc, "auth", "guardrails"); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	now := time.Now()
	job := &video.VideoJob{
		ID:             fmt.Sprintf("video-%s", rc.RequestID),
		OrgID:          orgID,
		KeyID:          keyID,
		Status:         video.StatusQueued,
		Model:          req.Model,
		Prompt:         req.Prompt,
		Duration:       req.Duration,
		Size:           req.Size,
		FPS:            req.FPS,
		NumVariants:    req.N,
		Style:          req.Style,
		AspectRatio:    req.AspectRatio,
		ClientMetadata: req.Metadata,
		CreatedAt:      now,
		ExpiresAt:      now.Add(24 * time.Hour),
	}

	if err := h.videoStore.Create(job); err != nil {
		models.WriteError(w, models.ErrInternal("failed to create video job: "+err.Error()))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(job.ToSubmitResponse())
}

// GetVideoStatus handles GET /v1/videos/{video_id}.
func (h *Handlers) GetVideoStatus(w http.ResponseWriter, r *http.Request) {
	if !h.videoGate(w) {
		return
	}

	videoID := r.URL.Query().Get("video_id")
	if videoID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_video_id", "video_id is required"))
		return
	}

	orgID, _, ok := h.videoCaller(w, r)
	if !ok {
		return
	}
	job, err := h.videoStore.GetForOrg(videoID, orgID)
	if err != nil {
		models.WriteError(w, errVideoNotFound())
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(job.ToStatusResponse())
}

// DeleteVideo handles DELETE /v1/videos/{video_id}.
func (h *Handlers) DeleteVideo(w http.ResponseWriter, r *http.Request) {
	if !h.videoGate(w) {
		return
	}

	videoID := r.URL.Query().Get("video_id")
	if videoID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_video_id", "video_id is required"))
		return
	}

	orgID, _, ok := h.videoCaller(w, r)
	if !ok {
		return
	}
	job, err := h.videoStore.GetForOrg(videoID, orgID)
	if err != nil {
		models.WriteError(w, errVideoNotFound())
		return
	}

	// Cancel if still running.
	status := "deleted"
	if !job.IsTerminal() {
		now := time.Now()
		job.Status = video.StatusCancelled
		job.CompletedAt = &now
		status = "cancelled"
		h.videoStore.Update(job)
	}

	h.videoStore.Delete(job.ID)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":      job.ID,
		"object":  "video",
		"status":  status,
		"deleted": true,
	})
}

// ListVideos handles GET /v1/videos.
func (h *Handlers) ListVideos(w http.ResponseWriter, r *http.Request) {
	if !h.videoGate(w) {
		return
	}

	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 100 {
			limit = n
		}
	}

	offset := 0
	if v := r.URL.Query().Get("offset"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 {
			offset = n
		}
	}

	filters := video.VideoListFilters{
		Status: r.URL.Query().Get("status"),
		Model:  r.URL.Query().Get("model"),
		Limit:  limit,
		Offset: offset,
		Order:  r.URL.Query().Get("order"),
	}
	if filters.Order == "" {
		filters.Order = "desc"
	}

	orgID, _, ok := h.videoCaller(w, r)
	if !ok {
		return
	}
	jobs, total, err := h.videoStore.ListByOrg(orgID, filters)
	if err != nil {
		models.WriteError(w, models.ErrInternal("failed to list videos: "+err.Error()))
		return
	}

	data := make([]map[string]interface{}, len(jobs))
	for i, j := range jobs {
		data[i] = j.ToStatusResponse()
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object":   "list",
		"data":     data,
		"total":    total,
		"limit":    limit,
		"offset":   offset,
		"has_more": total > offset+limit,
	})
}
