package server

import (
	"encoding/json"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// UploadFile handles POST /v1/files.
func (h *Handlers) UploadFile(w http.ResponseWriter, r *http.Request) {
	if h.fileStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "File storage is not configured",
		})
		return
	}

	// Parse multipart form (max 512MB).
	const maxFileSize = 512 << 20
	if err := r.ParseMultipartForm(maxFileSize); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_form", "Failed to parse multipart form: "+err.Error()))
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("missing_file", "file is required"))
		return
	}
	defer file.Close()

	purpose := r.FormValue("purpose")
	if purpose == "" {
		models.WriteError(w, models.ErrBadRequest("missing_purpose", "purpose is required"))
		return
	}

	fileData, err := io.ReadAll(io.LimitReader(file, maxFileSize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read file"))
		return
	}
	if int64(len(fileData)) > maxFileSize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "file_too_large",
			Message: "File exceeds maximum size of 512MB",
		})
		return
	}

	orgID := extractOrgID(r, h)

	meta := h.fileStore.Upload(orgID, header.Filename, purpose, fileData)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(meta)
}

// ListFiles handles GET /v1/files.
func (h *Handlers) ListFiles(w http.ResponseWriter, r *http.Request) {
	if h.fileStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "File storage is not configured",
		})
		return
	}

	orgID := extractOrgID(r, h)
	purpose := r.URL.Query().Get("purpose")

	files := h.fileStore.List(orgID, purpose)
	if files == nil {
		files = []models.FileObject{}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(models.FileListResponse{
		Object: "list",
		Data:   files,
	})
}

// GetFile handles GET /v1/files/{file_id}.
func (h *Handlers) GetFile(w http.ResponseWriter, r *http.Request) {
	if h.fileStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "File storage is not configured",
		})
		return
	}

	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}

	orgID := extractOrgID(r, h)

	stored := h.fileStore.Get(fileID, orgID)
	if stored == nil {
		models.WriteError(w, models.ErrNotFound("file_not_found", "File not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(stored.Meta)
}

// GetFileContent handles GET /v1/files/{file_id}/content.
func (h *Handlers) GetFileContent(w http.ResponseWriter, r *http.Request) {
	if h.fileStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "File storage is not configured",
		})
		return
	}

	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}

	orgID := extractOrgID(r, h)

	stored := h.fileStore.Get(fileID, orgID)
	if stored == nil {
		models.WriteError(w, models.ErrNotFound("file_not_found", "File not found"))
		return
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", "attachment; filename=\""+stored.Meta.Filename+"\"")
	w.WriteHeader(http.StatusOK)
	w.Write(stored.Content)
}

// DeleteFile handles DELETE /v1/files/{file_id}.
func (h *Handlers) DeleteFile(w http.ResponseWriter, r *http.Request) {
	if h.fileStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "File storage is not configured",
		})
		return
	}

	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}

	orgID := extractOrgID(r, h)

	if !h.fileStore.Delete(fileID, orgID) {
		models.WriteError(w, models.ErrNotFound("file_not_found", "File not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(models.FileDeleteResponse{
		ID:      fileID,
		Object:  "file",
		Deleted: true,
	})
}
