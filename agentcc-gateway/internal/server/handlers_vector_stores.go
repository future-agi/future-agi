package server

import (
	"context"
	"fmt"
	"net/http"
	"net/url"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// ---------------------------------------------------------------------------
// Helper: resolve VectorStoresProvider
// ---------------------------------------------------------------------------

func (h *Handlers) resolveVectorStoresProvider(ctx context.Context, rc *models.RequestContext) (providers.VectorStoresProvider, error) {
	orgID := rc.Metadata["org_id"]
	_, orgCfg := h.resolveOrgConfig(rc)
	provider, err := h.resolveProviderWithOrgFallback(ctx, rc, orgID, orgCfg, "gpt-4o")
	if err != nil {
		return nil, err
	}

	vsp, ok := provider.(providers.VectorStoresProvider)
	if !ok {
		return nil, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support the Vector Stores API", rc.Provider),
		}
	}
	return vsp, nil
}

// ---------------------------------------------------------------------------
// Helper: proxy a Vector Stores API request
// ---------------------------------------------------------------------------

func (h *Handlers) proxyVectorStores(w http.ResponseWriter, r *http.Request, method, upstreamPath string, body []byte, queryParams url.Values, operation string) {
	rc := h.setupAssistantsRC(r, operation)
	rc.EndpointType = "vector_stores"
	rc.Metadata["vector_stores_operation"] = operation
	defer rc.Release()

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	vsp, err := h.resolveVectorStoresProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	respBody, statusCode, respHeaders, proxyErr := vsp.ProxyVectorStoresRequest(ctx, method, upstreamPath, body, queryParams)
	if proxyErr != nil {
		models.WriteErrorFromError(w, proxyErr)
		return
	}

	h.setAgentccHeaders(w, rc)
	if respHeaders != nil {
		for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
			if v := respHeaders.Get(key); v != "" {
				w.Header().Set(key, v)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	if statusCode == 0 {
		statusCode = http.StatusOK
	}
	w.WriteHeader(statusCode)
	w.Write(respBody)
}

// ===========================================================================
// Vector Stores CRUD
// ===========================================================================

// CreateVectorStore handles POST /v1/vector_stores.
func (h *Handlers) CreateVectorStore(w http.ResponseWriter, r *http.Request) {
	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}
	h.proxyVectorStores(w, r, "POST", "/v1/vector_stores", body, nil, "vector_stores.create")
}

// ListVectorStores handles GET /v1/vector_stores.
func (h *Handlers) ListVectorStores(w http.ResponseWriter, r *http.Request) {
	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}
	h.proxyVectorStores(w, r, "GET", "/v1/vector_stores", nil, qp, "vector_stores.list")
}

// GetVectorStore handles GET /v1/vector_stores/{vector_store_id}.
func (h *Handlers) GetVectorStore(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	h.proxyVectorStores(w, r, "GET", "/v1/vector_stores/"+vsID, nil, nil, "vector_stores.get")
}

// UpdateVectorStore handles POST /v1/vector_stores/{vector_store_id}.
func (h *Handlers) UpdateVectorStore(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}
	h.proxyVectorStores(w, r, "POST", "/v1/vector_stores/"+vsID, body, nil, "vector_stores.update")
}

// DeleteVectorStore handles DELETE /v1/vector_stores/{vector_store_id}.
func (h *Handlers) DeleteVectorStore(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	h.proxyVectorStores(w, r, "DELETE", "/v1/vector_stores/"+vsID, nil, nil, "vector_stores.delete")
}

// SearchVectorStore handles POST /v1/vector_stores/{vector_store_id}/search.
func (h *Handlers) SearchVectorStore(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}
	h.proxyVectorStores(w, r, "POST", "/v1/vector_stores/"+vsID+"/search", body, nil, "vector_stores.search")
}

// ===========================================================================
// Vector Store Files
// ===========================================================================

// CreateVectorStoreFile handles POST /v1/vector_stores/{vector_store_id}/files.
func (h *Handlers) CreateVectorStoreFile(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}
	h.proxyVectorStores(w, r, "POST", "/v1/vector_stores/"+vsID+"/files", body, nil, "vector_stores.files.create")
}

// ListVectorStoreFiles handles GET /v1/vector_stores/{vector_store_id}/files.
func (h *Handlers) ListVectorStoreFiles(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before", "filter"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}
	h.proxyVectorStores(w, r, "GET", "/v1/vector_stores/"+vsID+"/files", nil, qp, "vector_stores.files.list")
}

// DeleteVectorStoreFile handles DELETE /v1/vector_stores/{vector_store_id}/files/{file_id}.
func (h *Handlers) DeleteVectorStoreFile(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	fileID := r.URL.Query().Get("file_id")
	if vsID == "" || fileID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id and file_id are required"))
		return
	}
	h.proxyVectorStores(w, r, "DELETE", "/v1/vector_stores/"+vsID+"/files/"+fileID, nil, nil, "vector_stores.files.delete")
}

// CreateVectorStoreFileBatch handles POST /v1/vector_stores/{vector_store_id}/file_batches.
func (h *Handlers) CreateVectorStoreFileBatch(w http.ResponseWriter, r *http.Request) {
	vsID := r.URL.Query().Get("vector_store_id")
	if vsID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "vector_store_id is required"))
		return
	}
	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}
	h.proxyVectorStores(w, r, "POST", "/v1/vector_stores/"+vsID+"/file_batches", body, nil, "vector_stores.file_batches.create")
}
