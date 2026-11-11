package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
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
	rc.GuardrailRequest = responsesGuardrailRequest(&req)

	// Extract client IP.
	rc.Metadata["client_ip"] = extractClientIP(r)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)

	// Caller dimensions, from the x-agentcc-metadata header and the body's metadata field.
	applyCallerMetadata(rc, r, req.Metadata)
	applyCallerExtras(rc, req.Extra)

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

	if orgID != "" {
		if orgProvider, providerID, upstreamModel, _ := h.orgProviderFor(orgID, orgCfg, req.Model); orgProvider != nil {
			provider = orgProvider
			rc.Provider = providerID
			rc.Metadata["org_provider_model_match"] = req.Model
			orgModelResolved = true
			if upstreamModel != req.Model {
				if body, err = rewriteRequestModel(body, upstreamModel); err != nil {
					models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
					return
				}
			}
		}
	}

	if !orgModelResolved {
		var upstreamModel string
		provider, upstreamModel, err = h.resolveProvider(ctx, rc, req.Model)
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
		} else if upstreamModel != req.Model {
			if body, err = rewriteRequestModel(body, upstreamModel); err != nil {
				models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
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
		applyResponsesUsage(callRC, body)
		callRC.GuardrailResponse = assistantText(responsesOutputText(body))
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

	// Post-plugins (cost, logging, etc.) run when the stream ends. Without a
	// terminal event there is no usage: the output relayed is estimated, and a
	// stream that relayed none goes unpriced rather than priced at zero.
	var streamed streamedChars
	finalize := func(detach bool) {
		if rc.Response == nil {
			rc.Response = &models.ChatCompletionResponse{Model: rc.ResolvedModel}
		}
		if rc.Response.Usage == nil && streamed > 0 {
			rc.Response.Usage = estimatedStreamUsage(rc, responsesPromptTokens(reqBody), streamed)
		}
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
	}

	// fail ends a stream that stopped before its terminal event, telling the
	// client why unless the client is the one that left.
	fail := func(err error) {
		if apiErr := streamEndError(ctx, err); apiErr != nil {
			rc.AddError(apiErr)
			if _, writeErr := w.Write(responsesStreamErrorEvent(apiErr)); writeErr == nil {
				flusher.Flush()
			}
		}
		finalize(ctx.Err() != nil)
	}

	// Relay the stream one event at a time, each as it arrived. The terminal
	// event carries the whole response, usage included, so it is held until
	// that usage is priced.
	events := streaming.NewEventReader(stream)
	for {
		ev, readErr := events.Next()
		if readErr != nil && readErr != io.EOF {
			// The event the error cut off is not forwarded: run into the error
			// event, it would hide that event from the client's parser.
			slog.Warn("error reading response stream",
				"request_id", rc.RequestID,
				"error", readErr,
			)
			fail(readErr)
			return
		}
		eventType := responsesEventType(ev)
		if strings.HasSuffix(eventType, ".delta") {
			streamed.addEvent(ev.Data())
		}
		if responsesTerminalEvents[eventType] {
			if data := ev.Data(); readErr == nil || json.Valid(data) {
				var terminal struct {
					Response json.RawMessage `json:"response"`
				}
				if json.Unmarshal(data, &terminal) == nil {
					applyResponsesUsage(rc, terminal.Response)
				}
				if eventType == "response.failed" {
					rc.AddError(upstreamStreamError(data))
				}
				endRelay(ctx, w, flusher, rc, ev, stream, finalize)
				return
			}
		}
		if eventType == "error" {
			rc.AddError(upstreamStreamError(ev.Data()))
		}
		if len(ev.Raw) > 0 {
			if _, writeErr := w.Write(ev.Raw); writeErr != nil {
				slog.Warn("error writing response stream",
					"request_id", rc.RequestID,
					"error", writeErr,
				)
				finalize(true)
				return
			}
			flusher.Flush()
		}
		switch {
		case readErr == io.EOF:
			finalize(false)
			return
		case ctx.Err() != nil:
			fail(ctx.Err())
			return
		}
	}
}

// responsesTerminalEvents end a Responses stream. Each carries the whole
// response, its usage included.
var responsesTerminalEvents = map[string]bool{
	"response.completed":  true,
	"response.incomplete": true,
	"response.failed":     true,
}

// responsesEventType is an event's type: its event: field, or the type in its
// payload for an upstream that sends data lines alone.
func responsesEventType(ev streaming.Event) string {
	if ev.Name != "" {
		return ev.Name
	}
	var payload struct {
		Type string `json:"type"`
	}
	_ = json.Unmarshal(ev.Data(), &payload)
	return payload.Type
}

// applyResponsesUsage moves a Responses API response's model and token counts
// onto rc, which is what the cost plugin prices from. A response without usage
// leaves rc.Response without it, so the request goes unpriced rather than
// priced at zero.
func applyResponsesUsage(rc *models.RequestContext, body []byte) {
	var parsed struct {
		Model string `json:"model"`
		Usage *struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
			TotalTokens  int `json:"total_tokens"`
		} `json:"usage"`
	}
	_ = json.Unmarshal(body, &parsed)
	if parsed.Model != "" {
		rc.ResolvedModel = parsed.Model
	}
	rc.Response = &models.ChatCompletionResponse{Model: rc.ResolvedModel}
	if parsed.Usage != nil {
		rc.Response.Usage = &models.Usage{
			PromptTokens:     parsed.Usage.InputTokens,
			CompletionTokens: parsed.Usage.OutputTokens,
			TotalTokens:      parsed.Usage.TotalTokens,
		}
	}
}

// responsesStreamErrorEvent is an error as a Responses stream event: the
// fields of OpenAI's error event, plus an error object. The object is what
// makes the OpenAI SDKs raise; without it they hand the event to code that
// may read only text deltas, and the failure goes unseen.
func responsesStreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(struct {
		Type    string             `json:"type"`
		Code    string             `json:"code"`
		Message string             `json:"message"`
		Param   *string            `json:"param"`
		Error   models.ErrorDetail `json:"error"`
	}{
		Type:    "error",
		Code:    apiErr.Code,
		Message: apiErr.Message,
		Param:   apiErr.Param,
		Error: models.ErrorDetail{
			Message: apiErr.Message,
			Type:    apiErr.Type,
			Param:   apiErr.Param,
			Code:    apiErr.Code,
		},
	})
	return fmt.Appendf(nil, "event: error\ndata: %s\n\n", data)
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
