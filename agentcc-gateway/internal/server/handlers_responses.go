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
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
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
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+models.DescribeJSONError(err)))
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
	// A background response is answered later, outside this creation
	// pipeline, so no output check here can read it: the guardrail
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

	if refusal := h.bindNativeEntry(r, rc, orgCfg, models.NativeResponses); refusal != nil {
		models.WriteError(w, refusal)
		return
	}
	// Resolve provider.
	var provider providers.Provider
	var orgModelResolved bool

	if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
		// A provider the org lists the model under (host != ""): this route does
		// not apply the org's model_map (resolveOrgProvider's rule), as before
		// the map was read.
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
				models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+models.DescribeJSONError(err)))
				return
			}
		}
	}
	// The body is passed through as written: one the org's own provider took
	// under the id it registered names that id.
	if sent := passThroughModel(rc, req.Model); sent != req.Model {
		if body, err = rewriteRequestModel(body, sent); err != nil {
			models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+models.DescribeJSONError(err)))
			return
		}
	}

	// Apply per-org provider override.
	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}
	// Resolve ownership from the final instance, including early org-model
	// matches and org overrides; these paths need not set billing_mode.
	rc.CredentialSource = credentialSourceOf(provider, orgID)
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
	if refusal := h.callCapGate(ctx, r, rc, body, orgCfg); refusal != nil {
		models.WriteError(w, refusal)
		return
	}

	// The request is passed through to the provider as the client sent it
	// (with the routed model), and rc.Request stays nil, as on the other
	// pass-through routes (see AnthropicMessages). The guardrail checks on
	// the request read the body the provider gets, in the chat shape, from
	// rc.PassThrough: a background request too, whose answer this pipeline
	// never gets but whose request it does. A check's rewrite of it (a
	// remediation) is written into the body sent.
	passThrough := toolview.Responses(body)
	rc.PassThrough = passThrough
	var sent struct {
		Model string `json:"model"`
	}
	if json.Unmarshal(body, &sent) != nil {
		models.WriteError(w, computerUseUnavailable())
		return
	}
	if refusal := h.attachNativeEntry(ctx, rc, orgCfg, passThrough, provider, nil, sent.Model); refusal != nil {
		models.WriteError(w, refusal)
		return
	}

	if req.Stream {
		h.handleResponseStream(ctx, w, rc, rp, passThrough, ownerOrgID, background)
	} else {
		h.handleResponseNonStream(ctx, w, rc, rp, passThrough, ownerOrgID, background)
	}
}

// refuseUnpricedBackground is the refusal of a background response paid
// from prepaid credit, which never pays for what the gateway cannot price: a
// background response's final usage never passes through this pipeline (it
// goes on after its stream or its queued answer, and is read back from the
// provider by read-only polling). The provider calls run it first, after
// the pre-plugins: the auth plugin has authenticated the key (a key it
// refuses gets its 401 or 403, never this 400), key_type is the
// authenticated key's, and the provider is never called.
func refuseUnpricedBackground(rc *models.RequestContext, background bool) error {
	if background && paidFromPrepaidCredit(rc) {
		return errUsageUnavailable()
	}
	return nil
}

func (h *Handlers) handleResponseNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, req models.NativeBody, ownerOrgID string, background bool) {
	// Wrap the provider call so it runs through the pipeline engine
	// (auth, rate limiting, budget, cost, logging, guardrails).
	var respBody []byte
	var continuation responseContinuation
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		if err := refuseUnpricedBackground(callRC, background); err != nil {
			return err
		}
		// The body after the checks on the request, with any rewrite of
		// theirs.
		dispatchCtx, nativeBody, attempt, refusal := h.beforeNativeDispatch(callCtx, callRC, req, rp, nil, "")
		if refusal != nil {
			return refusal
		}
		defer h.endNativeAttempt(attempt, capbudget.NativeAborted)
		continuation, refusal = h.prepareResponseContinuation(dispatchCtx, rp, nativeBody, ownerOrgID)
		if refusal != nil {
			return refusal
		}
		if background {
			retriever, ok := rp.(providers.ResponsesRetriever)
			if !ok || h.responsesStore == nil {
				return models.ErrBadRequest("background_not_supported", "Background responses require provider retrieval and response storage; use background:false")
			}
			continuation.retriever = retriever
		}
		body, _, err := rp.CreateResponse(dispatchCtx, nativeBody)
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
		if refusal := validateNativeResponse(callCtx, req, callRC.Routing.ComputerUse, body); refusal != nil {
			return refusal
		}
		if refusal := h.endNativeAttempt(attempt, capbudget.NativeCompleted); refusal != nil {
			return refusal
		}
		return nil
	}

	// An output check that withheld the answer is refused like a pre-stage
	// block, and respBody is never served.
	processErr := h.engine.Process(ctx, rc, providerCall)
	h.afterNativeBilling(context.WithoutCancel(ctx), rc)
	if err := pipelineErr(rc, processErr); err != nil {
		models.WriteErrorFromError(w, providerFailure(rc, err))
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

	h.rememberResponse(respBody, ownerOrgID, continuation, true)

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleResponseStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, rp providers.ResponsesProvider, req models.NativeBody, ownerOrgID string, background bool) {
	var nativeAttempt capbudget.NativeAttempt
	postComplete := false
	defer func() {
		h.endNativeAttempt(nativeAttempt, capbudget.NativeAborted)
		if nativeAttempt != nil && !postComplete {
			h.engine.RunPostPlugins(context.WithoutCancel(ctx), rc)
		}
		h.afterNativeBilling(context.WithoutCancel(ctx), rc)
	}()
	// Run pre-plugins (auth, rate limit, budget, guardrails) via engine.Process.
	// The provider call starts the stream; post-plugins run after the stream ends.
	var stream io.ReadCloser
	var continuation responseContinuation
	providerCall := func(callCtx context.Context, callRC *models.RequestContext) error {
		if err := refuseUnpricedBackground(callRC, background); err != nil {
			return err
		}
		// The body after the checks on the request, with any rewrite of
		// theirs.
		gate, refusal := prepareNativeStream(callCtx, req, callRC.Routing.ComputerUse)
		if refusal != nil {
			return refusal
		}
		dispatchCtx, body, attempt, refusal := h.beforeNativeDispatch(callCtx, callRC, req, rp, nil, "")
		if refusal != nil {
			return refusal
		}
		nativeAttempt = attempt
		opened := false
		defer func() {
			if !opened {
				h.endNativeAttempt(attempt, capbudget.NativeAborted)
			}
		}()
		continuation, refusal = h.prepareResponseContinuation(dispatchCtx, rp, body, ownerOrgID)
		if refusal != nil {
			return refusal
		}
		s, _, err := rp.StreamResponse(dispatchCtx, body)
		if err != nil {
			if s != nil {
				s.Close()
			}
			return err
		}
		stream = gate.wrap(dispatchCtx, s)
		opened = true
		callRC.IsStream = true
		return nil
	}

	// A cache hit an output check withheld is refused like a pre-stage block.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
		postComplete = true
		models.WriteErrorFromError(w, providerFailure(rc, err))
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
	closeStream := closeNativeStreamOnce(stream)
	defer closeStream()

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
	usage := responsesStreamUsage{onResponse: func(body []byte) {
		h.rememberResponse(body, ownerOrgID, continuation, false)
	}}
	// A budgeted key's stream is priced however it ends (CN22c): the text
	// its deltas carry is counted for an estimate. No other stream counts it.
	budgeted := h.keyBudgeted(rc)
	var output responsesStreamOutput

	// Relay the SSE stream directly from the provider.
	buf := make([]byte, 32*1024)
	ended := false // the upstream stream ended, rather than a read or a write failing
streamLoop:
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			answer.Write(buf[:n])
			usage.Write(buf[:n])
			if budgeted {
				output.Write(buf[:n])
			}
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
		} else if budgeted {
			// A key with a spend budget is priced on an estimate (CN22c
			// R-CN22c-1), or dropping each stream before its usage would
			// spend past the budget.
			estimateBudgetedResponsesStream(ctx, rc, &output)
			slog.Warn("responses stream reported no usage the gateway could read; the budgeted key's stream is priced on an estimate",
				"request_id", rc.RequestID, "model", rc.ResolvedModel, "stream_ended", ended,
				"line_over_limit", usage.skipped, "last_event_over_limit", usage.finalSkipped,
				"prompt_tokens", rc.Response.Usage.PromptTokens, "completion_tokens", rc.Response.Usage.CompletionTokens)
		} else {
			slog.Warn("responses stream reported no usage the gateway could read; it is not priced",
				"request_id", rc.RequestID, "model", rc.ResolvedModel, "stream_ended", ended,
				"line_over_limit", usage.skipped, "last_event_over_limit", usage.finalSkipped)
		}
	}
	answer.giveTo(rc)
	held.beforeChecks(rc, answer.complete())
	// Keep post-plugin settlement alive after a client leaves, including when
	// this answer is not held. Held answers also finish their blocking checks.
	if ctx.Err() != nil {
		ctx = context.WithoutCancel(ctx)
	}
	closeStream()
	nativeReason := capbudget.NativeAborted
	if ended {
		nativeReason = capbudget.NativeCompleted
	}
	h.endNativeAttempt(nativeAttempt, nativeReason)
	postComplete = true
	h.engine.RunPostPlugins(checksContext(ctx, held, false), rc)
	h.afterNativeBilling(context.WithoutCancel(ctx), rc)
	// Only publish a successful terminal response after output checks pass.
	// rememberResponse also enforces the request and response storage opt-outs
	// and keeps the same tenant and upstream binding as non-streamed responses.
	if ended && usage.completed && !usage.finalSkipped && pipelineErr(rc, nil) == nil {
		h.rememberResponse(usage.response, ownerOrgID, continuation, true)
	}
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

	if stored.Refresh != nil {
		// Ownership is checked before contacting the origin. Its HTTP client
		// and the incoming request context bound this single poll.
		body, err := stored.Refresh(r.Context())
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
		var updated struct {
			ID     string `json:"id"`
			Status string `json:"status"`
		}
		if json.Unmarshal(body, &updated) != nil || updated.ID != responseID {
			models.WriteError(w, models.ErrUpstreamProvider(http.StatusBadGateway, "Invalid background response from provider"))
			return
		}
		pending := false
		switch updated.Status {
		case "queued", "in_progress":
			pending = true
		case "completed", "failed", "incomplete", "cancelled":
		default:
			models.WriteError(w, models.ErrUpstreamProvider(http.StatusBadGateway, "Invalid background response status from provider"))
			return
		}
		// A terminal answer is stored and served only after the output checks
		// in force for this caller pass it; a refused answer is not stored, so
		// a later GET refreshes and checks it again.
		if !pending {
			if apiErr := h.checkRefreshedAnswer(r, orgID, body); apiErr != nil {
				models.WriteError(w, apiErr)
				return
			}
		}
		stored = h.responsesStore.Update(stored, body, pending)
		if stored == nil || len(stored.Body) == 0 {
			models.WriteError(w, models.ErrNotFound("response_not_found", "Response not found"))
			return
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(stored.Body)
}

// checkRefreshedAnswer runs the output checks on body, a background
// response's answer that GET refreshed to a terminal status, before it is
// stored or served. The create path cannot run them (the answer arrives
// later, so CreateResponse refuses a background request while a check on
// the answer would block it); the answer first passes the gateway here, and
// the checks in force for this caller now apply. Only the auth plugin's
// request stage, which writes the key whose policy the checks read, and the
// guardrail plugin's response stage run: nothing is priced, logged or
// counted again. A check that blocks the answer returns the create path's
// refusal (403 content_blocked); an answer no check can read is refused
// while a check on the answer would block it (403 answer_unavailable). Nil
// means the answer may be stored and served.
func (h *Handlers) checkRefreshedAnswer(r *http.Request, orgID string, body []byte) *models.APIError {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = models.GetRequestID(r.Context())
	rc.EndpointType = "responses"
	rc.Metadata["client_ip"] = middleware.ClientIP(r)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	setAuthMetadataFromRequest(rc, r)
	readGuardrailPolicyHeader(rc, r)
	if apiErr := h.engine.RunRequestPlugins(r.Context(), rc, "auth"); apiErr != nil {
		return apiErr
	}
	if answer, err := openai.ResponsesAnswer(body); err == nil {
		rc.Response = answer
	} else {
		markAnswerUnreadable(rc)
	}
	return h.engine.RunResponsePlugins(r.Context(), rc, "guardrails")
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
