package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// Rerank handles POST /v1/rerank. Its one upstream call is recorded as an
// attempt, and the search units the upstream billed are recorded on the
// request (models.RerankSearchUnitsMetadataKey) for the rerank price.
func (h *Handlers) Rerank(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "rerank"
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

	var req models.RerankRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.Query == "" {
		models.WriteError(w, models.ErrBadRequest("missing_query", "query is required"))
		return
	}
	if len(req.Documents) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_documents", "documents is required and must not be empty"))
		return
	}

	rc.Model = req.Model
	rc.RerankRequest = &req
	// The checks on the request read its text (rc.PassThrough, in the chat
	// shape), and a check's rewrite of it goes into the query and the documents, which the provider
	// gets; X-Guardrail-Policy applies here as on chat.
	rc.PassThrough = requestview.Rerank(&req)

	setAuthMetadataFromRequest(rc, r)
	applyCallerMetadata(rc, r, nil)
	readGuardrailPolicyHeader(rc, r)
	// The billed search units are the upstream's to report.
	delete(rc.Metadata, models.RerankSearchUnitsMetadataKey)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Peek key org_id before resolving org config.
	h.peekKeyOrgID(rc)

	// Resolve per-org config.
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	// Agent, run and step come only from the typed agent context, which
	// needs the org config for its framework aliases.
	if apiErr := h.parseAgentContext(rc, r, nil); apiErr != nil {
		models.WriteError(w, apiErr)
		return
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
				// The org's own key: its attempt is byok and never feeds the
				// node's shared model health.
				rc.Metadata["billing_mode"] = billingBYOK
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
			resolved := provider
			provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
			if provider != resolved {
				rc.Metadata["billing_mode"] = billingBYOK
			}
		}
	}
	if apiErr := h.keyProviderACL(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	// Type-assert to RerankProvider.
	rp, ok := provider.(providers.RerankProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support reranking", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		attemptCtx, finish := h.beginAttempt(ctx, rc, models.AttemptPrimary, "", rc.Provider, rc.RerankRequest.Model, credentialType(rc))
		resp, err := rp.Rerank(attemptCtx, rc.RerankRequest)
		if err == nil && resp == nil {
			err = models.ErrInternal("no response from provider")
		}
		if err != nil {
			finish(nil, nil, err)
			return err
		}
		finish(resp, nil, nil)
		if units := resp.SearchUnits(); units > 0 {
			rc.Metadata[models.RerankSearchUnitsMetadataKey] = strconv.Itoa(units)
		}
		rc.RerankResponse = resp
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.RerankResponse == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.RerankResponse)
}
