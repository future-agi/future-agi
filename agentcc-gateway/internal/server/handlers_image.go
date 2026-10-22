package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// CreateImage handles POST /v1/images/generations.
func (h *Handlers) CreateImage(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "image"
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

	var req models.ImageRequest
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

	rc.Model = req.Model
	rc.ImageRequest = &req
	// The checks on the request read its text (rc.PassThrough, in the chat
	// shape), and a check's rewrite of it goes into the prompt, which the provider
	// gets; X-Guardrail-Policy applies here as on chat.
	rc.PassThrough = requestview.Image(&req)

	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	readGuardrailPolicyHeader(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, upstreamModel, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID, upstream := h.resolveOrgProvider(rc, orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
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
	// The model shutdown calendar: this handler runs no eligibility,
	// so a model the serving host shut down is refused here, before any
	// upstream call.
	if apiErr := h.shutDownModel(orgCfg, rc.Provider, req.Model); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	// Type-assert to ImageProvider.
	ip, ok := provider.(providers.ImageProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support image generation", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := ip.CreateImage(ctx, rc.ImageRequest)
		if err != nil {
			return err
		}
		rc.ImageResponse = resp
		// The model sent, which a route may have put in place of the
		// requested one: x-agentcc-model-used, the request log and the
		// cost plugin's price (implicitAttempt) all read it.
		rc.ResolvedModel = rc.ImageRequest.Model
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.ImageResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.ImageResponse)
}
