package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Search handles POST /v1/search.
func (h *Handlers) Search(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "search"
	rc.RequestHeaders = cloneRequestHeaders(r)
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

	var req models.SearchRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if apiErr := req.Validate(); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	// Apply defaults.
	if req.MaxResults == 0 {
		req.MaxResults = 5
	}
	if req.MaxTokensPerPage == 0 {
		req.MaxTokensPerPage = 1024
	}

	// NOTE: do not overload rc.Model with req.SearchProvider — that would
	// trigger model-ACL in the auth plugin (403 on every search) and confuse
	// cost/RBAC/metrics plugins. The search provider is already carried
	// explicitly in rc.SearchRequest and passed to resolveProvider() below.
	rc.SearchRequest = &req

	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	// Peek key org_id before resolving org config.
	h.peekKeyOrgID(rc)

	// Resolve per-org config.
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider using search_provider as the provider key.
	provider, err := h.resolveProvider(ctx, rc, req.SearchProvider)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.SearchProvider); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to SearchProvider.
	sp, ok := provider.(providers.SearchProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support search", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := sp.Search(ctx, rc.SearchRequest)
		if err != nil {
			return err
		}
		rc.SearchResponse = resp
		return nil
	}

	if err := h.engine.Process(ctx, rc, provider, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.SearchResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from search provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.SearchResponse)
}
