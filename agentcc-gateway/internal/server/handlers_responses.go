package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// CreateResponse handles POST /v1/responses.
// Passes through to the OpenAI Responses API via the provider.
func (h *Handlers) CreateResponse(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "responses"
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

	// Minimal parse to extract model and stream flag.
	var req models.ResponsesRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}

	rc.Model = req.Model
	rc.IsStream = req.Stream
	rc.UserID = req.User

	// Extract client IP.
	rc.Metadata["client_ip"] = extractClientIP(r)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)

	// Extract Agentcc metadata (with security key blocklist).
	if meta := r.Header.Get("x-agentcc-metadata"); meta != "" {
		var m map[string]string
		if err := json.Unmarshal([]byte(meta), &m); err == nil {
			for k, v := range m {
				if isBlockedMetadataKey(k) {
					continue
				}
				rc.Metadata[k] = v
			}
		}
	}

	// Resolve timeout.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Peek key org_id before resolving org config (same as ChatCompletion).
	h.peekKeyOrgID(rc)

	// Resolve per-org config.
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgRoutingOverrides(orgCfg, rc)
	h.applyOrgCacheOverrides(orgCfg, rc)
	h.applyOrgRateLimitOverrides(orgCfg, rc)
	h.applyOrgBudgetOverrides(orgCfg, rc)
	h.applyOrgCostTrackingOverrides(orgCfg, rc)
	h.applyOrgIPACLOverrides(orgCfg, rc)
	h.applyOrgAlertingOverrides(orgCfg, rc)
	h.applyOrgPrivacyOverrides(orgCfg, rc)
	h.applyOrgToolPolicyOverrides(orgCfg, rc)
	h.applyOrgAuditOverrides(orgCfg, rc)
	h.applyOrgModelDatabaseOverrides(orgCfg, rc)
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	var provider providers.Provider
	var orgModelResolved bool

	if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
		for providerID, provCfg := range orgCfg.Providers {
			if provCfg == nil || !provCfg.Enabled || !provCfg.HasCredentials() {
				continue
			}
			for _, m := range provCfg.Models {
				if orgModelMatches(m, req.Model, providerID) {
					orgProvider, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, provCfg.APIKey, provCfg)
					if err == nil {
						provider = orgProvider
						rc.Provider = providerID
						rc.Metadata["org_provider_model_match"] = req.Model
						orgModelResolved = true
						break
					}
				}
			}
			if orgModelResolved {
				break
			}
		}
	}

	if !orgModelResolved {
		provider, err = h.resolveProvider(ctx, rc, req.Model)
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
		}
	}

	// Apply per-org provider override.
	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to ResponsesProvider.
	rp, ok := provider.(providers.ResponsesProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support the Responses API", rc.Provider),
		})
		return
	}

	if req.Stream {
		h.handleResponseStream(ctx, w, rc, rp, body, orgID)
	} else {
		h.handleResponseNonStream(ctx, w, rc, rp, body, orgID)
	}
}

func (h *Handlers) handleResponseNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, reqBody []byte, orgID string) {
	// Wrap the provider call so it runs through the pipeline engine
	// (auth, rate limiting, budget, cost, logging, guardrails).
	var respBody []byte
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		body, _, err := rp.CreateResponse(callCtx, reqBody)
		if err != nil {
			return err
		}
		respBody = body

		// Parse usage from the response for cost tracking.
		var parsed struct {
			Model string `json:"model"`
			Usage *struct {
				InputTokens  int `json:"input_tokens"`
				OutputTokens int `json:"output_tokens"`
				TotalTokens  int `json:"total_tokens"`
			} `json:"usage"`
		}
		if json.Unmarshal(body, &parsed) == nil && parsed.Usage != nil {
			if parsed.Model != "" {
				callRC.ResolvedModel = parsed.Model
			}
			callRC.Response = &models.ChatCompletionResponse{
				Model: callRC.ResolvedModel,
				Usage: &models.Usage{
					PromptTokens:     parsed.Usage.InputTokens,
					CompletionTokens: parsed.Usage.OutputTokens,
					TotalTokens:      parsed.Usage.TotalTokens,
				},
			}
		} else {
			if parsed.Model != "" {
				callRC.ResolvedModel = parsed.Model
			}
			callRC.Response = &models.ChatCompletionResponse{
				Model: callRC.ResolvedModel,
			}
		}
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Short-circuit check (e.g., cache hit).
	if rc.Flags.ShortCircuited && rc.Response != nil {
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(rc.Response)
		return
	}

	if respBody == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	// Store the response for later retrieval if a store is configured.
	if h.responsesStore != nil {
		var parsed struct {
			ID    string `json:"id"`
			Store *bool  `json:"store"`
		}
		if err := json.Unmarshal(respBody, &parsed); err == nil && parsed.ID != "" {
			shouldStore := true
			if parsed.Store != nil {
				shouldStore = *parsed.Store
			}
			if shouldStore {
				h.responsesStore.Put(parsed.ID, orgID, respBody)
			}
		}
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleResponseStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, reqBody []byte, orgID string) {
	// Run pre-plugins (auth, rate limit, budget, guardrails) via engine.Process.
	// The provider call starts the stream; post-plugins run after the stream ends.
	var stream io.ReadCloser
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		s, _, err := rp.StreamResponse(callCtx, reqBody)
		if err != nil {
			return err
		}
		stream = s
		callRC.IsStream = true
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Short-circuit check (e.g., cache hit).
	if rc.Flags.ShortCircuited && rc.Response != nil {
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(rc.Response)
		return
	}

	if stream == nil {
		models.WriteError(w, models.ErrInternal("no stream from provider"))
		return
	}
	defer stream.Close()

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}
	flusher.Flush()

	// Relay the SSE stream directly from the provider.
	buf := make([]byte, 32*1024)
streamLoop:
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			if _, writeErr := w.Write(buf[:n]); writeErr != nil {
				slog.Warn("error writing response stream",
					"request_id", rc.RequestID,
					"error", writeErr,
				)
				break streamLoop
			}
			flusher.Flush()
		}
		if readErr != nil {
			if readErr != io.EOF {
				slog.Warn("error reading response stream",
					"request_id", rc.RequestID,
					"error", readErr,
				)
			}
			break streamLoop
		}

		// Check for context cancellation.
		select {
		case <-ctx.Done():
			break streamLoop
		default:
		}
	}

	// Run post-plugins (cost, logging, etc.) after stream completes.
	rc.Response = &models.ChatCompletionResponse{Model: rc.ResolvedModel}
	h.engine.RunPostPlugins(ctx, rc)
}

// GetResponse handles GET /v1/responses/{id}.
func (h *Handlers) GetResponse(w http.ResponseWriter, r *http.Request) {
	if h.responsesStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Response storage is not configured",
		})
		return
	}

	responseID := r.URL.Query().Get("id")
	if responseID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "response id is required"))
		return
	}

	// Extract org ID for scoping.
	orgID := extractOrgID(r, h)

	stored := h.responsesStore.Get(responseID, orgID)
	if stored == nil {
		models.WriteError(w, models.ErrNotFound("response_not_found", "Response not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(stored.Body)
}

// DeleteResponse handles DELETE /v1/responses/{id}.
func (h *Handlers) DeleteResponse(w http.ResponseWriter, r *http.Request) {
	if h.responsesStore == nil {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_configured",
			Message: "Response storage is not configured",
		})
		return
	}

	responseID := r.URL.Query().Get("id")
	if responseID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "response id is required"))
		return
	}

	orgID := extractOrgID(r, h)

	deleted := h.responsesStore.Delete(responseID, orgID)
	if !deleted {
		models.WriteError(w, models.ErrNotFound("response_not_found", "Response not found"))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"id":      responseID,
		"deleted": true,
	})
}

// extractOrgID extracts the org ID from auth metadata.
func extractOrgID(r *http.Request, h *Handlers) string {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	setAuthMetadataFromRequest(rc, r)

	h.peekKeyOrgID(rc)
	_, orgCfg := h.resolveOrgConfig(rc)
	if orgCfg != nil {
		if v, ok := rc.Metadata["org_id"]; ok {
			return v
		}
	}
	return ""
}
