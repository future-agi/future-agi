package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
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

	// A stored response belongs to the org of the key KeyAuth authenticated,
	// the same org GetResponse and DeleteResponse check.
	ownerOrgID, ok := h.callerOrgID(w, r)
	if !ok {
		return
	}

	rc.Model = req.Model
	rc.IsStream = req.Stream
	rc.UserID = req.User
	// A background response is answered later, and its answer never passes
	// through the gateway, so no output check can read it: the guardrail
	// plugin refuses the request when a check on the answer would block.
	background := false
	if raw, ok := req.Extra["background"]; ok && bytes.Equal(bytes.TrimSpace(raw), []byte("true")) {
		background = true
		rc.MarkAnswerUnavailable(answerOfBackgroundResponse)
	}

	// Extract client IP.
	rc.Metadata["client_ip"] = middleware.ClientIP(r)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)
	readGuardrailPolicyHeader(rc, r)

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

	if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
		// A provider the org lists the model under (host != ""): this route does
		// not apply the org's model_map (resolveOrgProvider's rule).
		if orgProvider, providerID, host, _ := h.orgProviderFor(orgID, orgCfg, req.Model); orgProvider != nil && host != "" {
			provider = orgProvider
			rc.Provider = providerID
			rc.Metadata["org_provider_model_match"] = req.Model
			noteOrgUpstreamModel(rc, req.Model, host)
			orgModelResolved = true
		}
	}

	if !orgModelResolved {
		var upstreamModel string
		provider, upstreamModel, err = h.resolveProvider(ctx, rc, req.Model)
		if err != nil {
			if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
				if orgP, providerID, _ := h.resolveOrgProvider(rc, orgID, orgCfg, req.Model); orgP != nil {
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
	// The body is passed through as written: one the org's own provider took
	// under the id it registered names that id.
	if sent := passThroughModel(rc, req.Model); sent != req.Model {
		if body, err = rewriteRequestModel(body, sent); err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
			return
		}
	}

	// Apply per-org provider override.
	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}
	if apiErr := h.keyProviderACL(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return
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

	// The request is passed through to the provider as the client sent it
	// (with the routed model), and rc.Request stays nil, as on the other
	// pass-through routes (see AnthropicMessages). The guardrail checks on
	// the request read the body the provider gets, in the chat shape, from
	// rc.PassThrough: a background request too, whose answer the gateway
	// never gets but whose request it does. A check's rewrite of it (a
	// remediation) is written into the body sent.
	passThrough := requestview.Responses(body)
	rc.PassThrough = passThrough

	if req.Stream {
		h.handleResponseStream(ctx, w, rc, rp, passThrough, orgID, background)
	} else {
		h.handleResponseNonStream(ctx, w, rc, rp, passThrough, ownerOrgID, background)
	}
}

// refuseUnpricedBackground is the refusal of a background response paid
// from prepaid credit, which never pays for what the gateway cannot price: a
// background response's usage never passes through the gateway (the response
// goes on after its stream or its queued answer, and is read back from the
// provider, never here). The provider calls run it first, so it comes after
// the pre-plugins: the auth plugin has authenticated the key (a key it
// refuses gets its 401 or 403, never this 400), key_type is the
// authenticated key's, and the provider is never called.
func refuseUnpricedBackground(rc *models.RequestContext, background bool) error {
	if background && paidFromPrepaidCredit(rc) {
		return errUsageUnavailable()
	}
	return nil
}

func (h *Handlers) handleResponseNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, req *requestview.View, ownerOrgID string, background bool) {
	// Wrap the provider call so it runs through the pipeline engine
	// (auth, rate limiting, budget, cost, logging, guardrails).
	var respBody []byte
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		if err := refuseUnpricedBackground(callRC, background); err != nil {
			return err
		}
		// The body after the checks on the request, with any rewrite of
		// theirs.
		body, _, err := rp.CreateResponse(callCtx, req.Body())
		if err != nil {
			return err
		}
		respBody = body

		// The response's usage prices it (the cost plugin).
		model, usage := responsesUsage(body)
		if model != "" {
			callRC.ResolvedModel = model
		}
		callRC.Response = &models.ChatCompletionResponse{Model: callRC.ResolvedModel, Usage: usage}
		// An output check reads the answer the client gets, not only its
		// usage; one it cannot read is refused when a check would block.
		if callRC.Flags.AnswerReaders != 0 {
			if answer, err := openai.ResponsesAnswer(body); err == nil {
				giveChecksTheAnswer(callRC, answer)
			} else {
				markAnswerUnreadable(callRC)
			}
		}
		return nil
	}

	// An output check that withheld the answer is refused like a pre-stage
	// block, and respBody is never served.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
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
			if shouldStore && !h.responsesStore.Put(parsed.ID, ownerOrgID, respBody) {
				slog.Warn("response not stored: its id is held by another org's stored response",
					"response_id", parsed.ID, "org_id", ownerOrgID)
			}
		}
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleResponseStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, req *requestview.View, orgID string, background bool) {
	// Run pre-plugins (auth, rate limit, budget, guardrails) via engine.Process.
	// The provider call starts the stream; post-plugins run after the stream ends.
	var stream io.ReadCloser
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		if err := refuseUnpricedBackground(callRC, background); err != nil {
			return err
		}
		// The body after the checks on the request, with any rewrite of
		// theirs.
		s, _, err := rp.StreamResponse(callCtx, req.Body())
		if err != nil {
			return err
		}
		stream = s
		callRC.IsStream = true
		return nil
	}

	// A cache hit an output check withheld is refused like a pre-stage block.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
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

	// A stream that a blocking check on the answer reads is held until the
	// checks have passed the whole answer (heldStream).
	var held *heldStream
	if holdsTheStream(rc) {
		if held = newHeldStream(w); held != nil {
			w = held
		}
	}
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

	// The text the stream sends, for an output check that reads the answer,
	// and the usage its last event reports, which prices it.
	answer := newSSEAnswer(rc.Flags.AnswerReaders != 0, responsesStreamDecoder())
	if held != nil {
		answer.holdAll()
	}
	var usage responsesStreamUsage

	// Relay the SSE stream directly from the provider.
	buf := make([]byte, 32*1024)
	ended := false // the upstream stream ended, rather than a read or a write failing
streamLoop:
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			answer.Write(buf[:n])
			usage.Write(buf[:n])
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
			ended = readErr == io.EOF
			break streamLoop
		}

		// Check for context cancellation.
		select {
		case <-ctx.Done():
			break streamLoop
		default:
		}
	}

	// Run post-plugins (cost, logging, etc.) after stream completes, on the
	// usage the stream reported, as a non-streamed response is priced.
	if !usage.applyTo(rc) {
		if usage.finalSkipped && paidFromPrepaidCredit(rc) {
			// The last event is over the line limit: prepaid credit is
			// charged the hold, never 0.
			h.chargeTheHold(ctx, rc)
			slog.Warn("responses stream's last event is over the line limit its usage is read within; prepaid credit is charged the hold",
				"request_id", rc.RequestID, "model", rc.Model, "line_limit_bytes", maxResponsesUsageLineBytes,
				"prompt_tokens", rc.Response.Usage.PromptTokens, "completion_tokens", rc.Response.Usage.CompletionTokens)
		} else {
			slog.Warn("responses stream reported no usage the gateway could read",
				"request_id", rc.RequestID, "model", rc.ResolvedModel, "stream_ended", ended,
				"line_over_limit", usage.skipped, "last_event_over_limit", usage.finalSkipped)
		}
	}
	answer.giveTo(rc)
	held.beforeChecks(rc, answer.complete())
	h.engine.RunPostPlugins(checksContext(ctx, held, false), rc)
	if ended {
		endStream(w, flusher, held, rc, responsesStreamErrorEvent)
	} else {
		settleHeldStream(held, rc, responsesStreamErrorEvent)
	}
}

// responsesStreamErrorEvent is apiErr as a Responses API stream's error
// event: event "error", data {"type":"error","code","message","param"}.
func responsesStreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(map[string]any{
		"type":    "error",
		"code":    apiErr.Code,
		"message": apiErr.Message,
		"param":   nil,
	})
	return []byte("event: error\ndata: " + string(data) + "\n\n")
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

	orgID, ok := h.callerOrgID(w, r)
	if !ok {
		return
	}
	// Another org's response answers 404, the same as a missing one.
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

	orgID, ok := h.callerOrgID(w, r)
	if !ok {
		return
	}
	// Another org's response answers 404, the same as a missing one.
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
