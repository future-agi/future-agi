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

// OCR handles POST /v1/ocr.
func (h *Handlers) OCR(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "ocr"
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

	var req models.OCRRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if apiErr := req.Validate(); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	rc.Model = req.Model
	rc.OCRRequest = &req

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout (longer default for OCR).
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

	// Resolve provider from model.
	provider, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
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

	// Type-assert to OCRProvider.
	op, ok := provider.(providers.OCRProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support OCR", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := op.OCR(ctx, rc.OCRRequest)
		if err != nil {
			return err
		}
		rc.OCRResponse = resp
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.OCRResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from OCR provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.OCRResponse)
}
