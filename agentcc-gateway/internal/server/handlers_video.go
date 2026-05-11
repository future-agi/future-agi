package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

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
	if h.videoStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Video generation is not configured",
		})
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

	now := time.Now()
	job := &video.VideoJob{
		ID:             fmt.Sprintf("video-%s", rc.RequestID),
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
	if h.videoStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Video generation is not configured",
		})
		return
	}

	videoID := r.URL.Query().Get("video_id")
	if videoID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_video_id", "video_id is required"))
		return
	}

	job, err := h.videoStore.Get(videoID)
	if err != nil {
		models.WriteError(w, models.ErrNotFound("video_not_found", "Video job not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(job.ToStatusResponse())
}

// DeleteVideo handles DELETE /v1/videos/{video_id}.
func (h *Handlers) DeleteVideo(w http.ResponseWriter, r *http.Request) {
	if h.videoStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Video generation is not configured",
		})
		return
	}

	videoID := r.URL.Query().Get("video_id")
	if videoID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_video_id", "video_id is required"))
		return
	}

	job, err := h.videoStore.Get(videoID)
	if err != nil {
		models.WriteError(w, models.ErrNotFound("video_not_found", "Video job not found"))
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
	if h.videoStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Video generation is not configured",
		})
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

	// Use empty orgID for now (no org filtering in basic implementation).
	jobs, total, err := h.videoStore.ListByOrg("", filters)
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
