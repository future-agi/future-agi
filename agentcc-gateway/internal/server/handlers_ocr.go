package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
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
		// A field sent twice or in another case has its own 400
		// (duplicate_field), so the gateway and the upstream read one value.
		var apiErr *models.APIError
		if errors.As(err, &apiErr) {
			models.WriteError(w, apiErr)
			return
		}
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if apiErr := req.Validate(); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	rc.Model = req.Model
	rc.OCRRequest = &req
	// What the model reads is the document, which no check on the request
	// can read (rc.PassThrough says so): a check that would block or
	// rewrite the request refuses it before the provider
	// (request_unavailable). X-Guardrail-Policy applies here as on chat.
	rc.PassThrough = requestview.OCR(&req)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	readGuardrailPolicyHeader(rc, r)
	// The processed-page count is the cost plugin's to record.
	delete(rc.Metadata, models.OCRPagesMetadataKey)

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
	h.resolveContentMode(rc, orgCfg)
	req.KeepText = rc.Privacy.ContentMode == models.ContentModeFull && orgOptedIntoFullContent(orgCfg)

	// Resolve provider from model.
	provider, upstreamModel, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID, upstream := h.resolveOrgProvider(rc, orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				rc.Metadata["billing_mode"] = billingBYOK // the org's own key
				req.Model = upstream
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else {
		req.Model = upstreamModel
		if shouldApplyOrgProviderOverride(rc) {
			provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
		}
	}
	if apiErr := h.keyProviderACL(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return
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
		// The model sent, which a route may have put in place of the
		// requested one: x-agentcc-model-used, the request log and the
		// cost plugin's price (implicitAttempt) all read it.
		rc.ResolvedModel = rc.OCRRequest.Model
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
	// Same-shape upstreams (Mistral direct, Azure- and Vertex-hosted
	// Mistral) get their own body back: the gateway decoded it only to meter
	// it, and re-encoding would drop every field it does not model.
	if raw := rc.OCRResponse.Raw(); len(raw) > 0 {
		w.Write(raw)
		return
	}
	json.NewEncoder(w).Encode(rc.OCRResponse)
}

// orgOptedIntoFullContent reports whether the org itself set
// privacy.content_mode to full (and keeps bodies), rather than inheriting the
// default: the opt-in an OCR request needs before its payloads may carry the
// document's extracted text.
func orgOptedIntoFullContent(orgCfg *tenant.OrgConfig) bool {
	if orgCfg == nil || orgCfg.Privacy == nil {
		return false
	}
	return strings.EqualFold(strings.TrimSpace(orgCfg.Privacy.ContentMode), string(models.ContentModeFull)) &&
		orgCfg.ContentMode() == string(models.ContentModeFull)
}
