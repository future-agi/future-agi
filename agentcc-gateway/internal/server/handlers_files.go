package server

import (
	"context"
	"encoding/json"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/files"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The Files API is served from the gateway's own file store; no provider is
// called. Each request runs the pipeline as a request to a model endpoint
// does (runPassedThrough): key authentication, rate limits and the request
// log apply, and a refused upload is never read.

// errFilesNotConfigured answers the Files API on a gateway with no file store.
func errFilesNotConfigured() *models.APIError {
	return &models.APIError{
		Status:  http.StatusNotImplemented,
		Type:    models.ErrTypeServer,
		Code:    "not_configured",
		Message: "File storage is not configured",
	}
}

// serveFiles runs a Files API request (operation) through the pipeline and,
// once call has served it, writes the JSON answer it returned with 200, or
// the error it was refused with. call gets the org of the key KeyAuth
// authenticated (callerOrgID's rule).
func (h *Handlers) serveFiles(w http.ResponseWriter, r *http.Request, operation string, call func(orgID string) (any, error)) {
	if h.fileStore == nil {
		models.WriteError(w, errFilesNotConfigured())
		return
	}
	rc := h.setupPassedThroughRC(r, "files", operation)
	defer rc.Release()

	var answer any
	err := h.runPassedThrough(r.Context(), r, rc, nil, nil, func(context.Context, []byte) (int, error) {
		id, ok := h.callerIdentity(r)
		if !ok {
			return 0, models.ErrUnauthorized("Invalid or missing API key")
		}
		out, callErr := call(id.OrgID)
		if callErr != nil {
			return 0, callErr
		}
		answer = out
		return http.StatusOK, nil
	})
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	if answer == nil {
		models.WriteError(w, errNoPassedThroughAnswer())
		return
	}
	if content, ok := answer.(*files.StoredFile); ok {
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Disposition", "attachment; filename=\""+content.Meta.Filename+"\"")
		w.WriteHeader(http.StatusOK)
		w.Write(content.Content)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(answer)
}

// errFileNotFound answers a file that is missing or another org's: the
// same 404 for both.
func errFileNotFound() *models.APIError {
	return models.ErrNotFound("file_not_found", "File not found")
}

// UploadFile handles POST /v1/files.
func (h *Handlers) UploadFile(w http.ResponseWriter, r *http.Request) {
	h.serveFiles(w, r, "files.upload", func(orgID string) (any, error) {
		// Parse multipart form (max 512MB).
		const maxFileSize = 512 << 20
		if err := r.ParseMultipartForm(maxFileSize); err != nil {
			return nil, models.ErrBadRequest("invalid_form", "Failed to parse multipart form: "+err.Error())
		}

		file, header, err := r.FormFile("file")
		if err != nil {
			return nil, models.ErrBadRequest("missing_file", "file is required")
		}
		defer file.Close()

		purpose := r.FormValue("purpose")
		if purpose == "" {
			return nil, models.ErrBadRequest("missing_purpose", "purpose is required")
		}

		fileData, err := io.ReadAll(io.LimitReader(file, maxFileSize+1))
		if err != nil {
			return nil, models.ErrBadRequest("read_error", "Failed to read file")
		}
		if int64(len(fileData)) > maxFileSize {
			return nil, &models.APIError{
				Status:  http.StatusRequestEntityTooLarge,
				Type:    models.ErrTypeInvalidRequest,
				Code:    "file_too_large",
				Message: "File exceeds maximum size of 512MB",
			}
		}
		return h.fileStore.Upload(orgID, header.Filename, purpose, fileData), nil
	})
}

// ListFiles handles GET /v1/files.
func (h *Handlers) ListFiles(w http.ResponseWriter, r *http.Request) {
	purpose := r.URL.Query().Get("purpose")
	h.serveFiles(w, r, "files.list", func(orgID string) (any, error) {
		list := h.fileStore.List(orgID, purpose)
		if list == nil {
			list = []models.FileObject{}
		}
		return models.FileListResponse{Object: "list", Data: list}, nil
	})
}

// GetFile handles GET /v1/files/{file_id}.
func (h *Handlers) GetFile(w http.ResponseWriter, r *http.Request) {
	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}
	h.serveFiles(w, r, "files.get", func(orgID string) (any, error) {
		// Another org's file answers 404, the same as a missing one.
		stored := h.fileStore.Get(fileID, orgID)
		if stored == nil {
			return nil, errFileNotFound()
		}
		return stored.Meta, nil
	})
}

// GetFileContent handles GET /v1/files/{file_id}/content.
func (h *Handlers) GetFileContent(w http.ResponseWriter, r *http.Request) {
	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}
	h.serveFiles(w, r, "files.content", func(orgID string) (any, error) {
		// Another org's file answers 404, the same as a missing one.
		stored := h.fileStore.Get(fileID, orgID)
		if stored == nil {
			return nil, errFileNotFound()
		}
		return stored, nil
	})
}

// DeleteFile handles DELETE /v1/files/{file_id}.
func (h *Handlers) DeleteFile(w http.ResponseWriter, r *http.Request) {
	fileID := r.URL.Query().Get("file_id")
	if fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_file_id", "file_id is required"))
		return
	}
	h.serveFiles(w, r, "files.delete", func(orgID string) (any, error) {
		// Another org's file answers 404, the same as a missing one.
		if !h.fileStore.Delete(fileID, orgID) {
			return nil, errFileNotFound()
		}
		return models.FileDeleteResponse{ID: fileID, Object: "file", Deleted: true}, nil
	})
}
