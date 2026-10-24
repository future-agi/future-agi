package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
)

// TextCompletion handles POST /v1/completions.
// Converts the legacy text completion format to chat format internally,
// then converts the response back to the legacy format.
func (h *Handlers) TextCompletion(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "completion"
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

	var req models.CompletionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	// Validate required fields.
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.Prompt == nil || len(req.Prompt) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_prompt", "prompt is required"))
		return
	}

	// Convert to chat format internally as a fallback for providers that don't
	// implement nativeTextCompletionProvider. If the resolved provider IS an
	// OpenAI-compat provider, we forward the raw CompletionRequest to upstream
	// /v1/completions instead (see handleCompletionNative below). Required for
	// legacy-only models like gpt-3.5-turbo-instruct which reject
	// /v1/chat/completions with "not a chat model".
	chatReq := req.ToChatRequest()

	rc.Model = req.Model
	rc.Request = chatReq
	rc.IsStream = req.Stream
	rc.UserID = req.User
	// Only a client that asked for usage gets a stream's usage chunk
	// (handleCompletionStream), as on /v1/chat/completions; another gets the
	// usage on the last text chunk.
	rc.Flags.ClientIncludeUsage = req.Stream && req.StreamOptions != nil && req.StreamOptions.IncludeUsage

	// Extract client IP for audit.
	rc.Metadata["client_ip"] = middleware.ClientIP(r)

	setAuthMetadataFromRequest(rc, r)

	// Caller dimensions, from the x-agentcc-metadata header.
	applyCallerMetadata(rc, r, nil)
	if sid := r.Header.Get("x-agentcc-session-id"); sid != "" {
		if len(sid) > maxSessionIDLen {
			models.WriteError(w, models.ErrBadRequest("session_id_too_long",
				fmt.Sprintf("x-agentcc-session-id exceeds maximum length of %d characters", maxSessionIDLen)))
			return
		}
		rc.SessionID = sid
	}

	// Extract guardrail policy override header.
	readGuardrailPolicyHeader(rc, r)

	// Extract cache-related headers.
	if v := r.Header.Get("x-agentcc-cache-ttl"); v != "" {
		rc.Metadata["cache_ttl"] = v
	}
	if v := r.Header.Get("x-agentcc-cache-namespace"); v != "" {
		rc.Metadata["cache_namespace"] = v
	}
	if r.Header.Get("x-agentcc-cache-force-refresh") == "true" {
		rc.Metadata["cache_force_refresh"] = "true"
	}
	if v := r.Header.Get("Cache-Control"); v != "" {
		rc.Metadata["cache_control"] = v
	}

	// Mark this as a completions-format request for downstream.
	rc.Metadata["endpoint_format"] = "completions"

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
	provider, upstreamModel, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		// If global registry doesn't know this model, try org provider model lists.
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID, upstream := h.resolveOrgProvider(rc, orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				req.Model = upstream
				chatReq.Model = upstream
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else {
		req.Model = upstreamModel
		chatReq.Model = upstreamModel
		if shouldApplyOrgProviderOverride(rc) {
			provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
		}
	}

	// Enforce provider ACL.
	if allowedCSV, ok := rc.Metadata["auth_allowed_providers"]; ok && rc.Provider != "" {
		allowed := false
		for _, p := range splitCSV(allowedCSV) {
			if p == rc.Provider {
				allowed = true
				break
			}
		}
		if !allowed {
			models.WriteError(w, models.ErrForbidden("API key does not have access to provider: "+rc.Provider))
			return
		}
	}

	// Native passthrough: OpenAI-compat providers get the legacy /v1/completions
	// request forwarded verbatim so legacy-only models (gpt-3.5-turbo-instruct,
	// davinci-002, babbage-002) work. Other providers fall back to
	// chat-translation. Streaming isn't implemented for the native path yet;
	// rather than silently chat-translating a legacy model and hitting the
	// upstream's "not a chat model" error, we reject upfront.
	if req.Stream {
		if isLegacyCompletionModel(req.Model) {
			models.WriteError(w, &models.APIError{
				Status:  http.StatusNotImplemented,
				Type:    models.ErrTypeServer,
				Code:    "streaming_not_supported",
				Message: fmt.Sprintf("Model %q only supports non-streaming /v1/completions. Set stream=false or use /v1/chat/completions with a chat-capable model.", req.Model),
			})
			return
		}
		h.handleCompletionStream(ctx, w, rc, provider)
	} else if native, ok := provider.(nativeTextCompletionProvider); ok {
		h.handleCompletionNative(ctx, w, rc, native, &req)
	} else {
		h.handleCompletionNonStream(ctx, w, rc, provider)
	}

	// Record health metrics.
	h.recordProviderHealth(rc)

	// Record provider latency for routing.
	if router := h.registry.Router(); router != nil {
		if d, ok := rc.Timings["provider"]; ok {
			router.RecordLatency(rc.Provider, d)
		}
	}
}

// nativeTextCompletionProvider is implemented by providers that can serve
// /v1/completions directly (currently only the openai-compat provider).
type nativeTextCompletionProvider interface {
	TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error)
}

// isLegacyCompletionModel returns true for models that only expose the
// legacy /v1/completions surface and reject /v1/chat/completions.
func isLegacyCompletionModel(model string) bool {
	switch model {
	case "gpt-3.5-turbo-instruct", "gpt-3.5-turbo-instruct-0914",
		"davinci-002", "babbage-002":
		return true
	}
	return false
}

// handleCompletionNative forwards the raw legacy request to the provider's
// upstream /v1/completions endpoint. The wire response sent to the caller is
// the native CompletionResponse shape, but rc.Response is populated with an
// equivalent ChatCompletionResponse so downstream post-plugins
// (cost/credits/logging/prometheus) that key off rc.Response.Usage and
// rc.Response.Model keep working uniformly — they don't need to branch on
// completion vs chat format. It holds every choice the client gets (a request
// with n above 1, or with a list of prompts, gets several), so an output check
// reads each of them, and the log shows them all.
func (h *Handlers) handleCompletionNative(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider nativeTextCompletionProvider, req *models.CompletionRequest) {
	var nativeResp *models.CompletionResponse

	// The provider gets req's prompt, not rc.Request, which the checks on
	// the request read: a check's rewrite of rc.Request (a remediation) is
	// written into req's prompt, or the request is refused when it cannot be.
	rc.PassThrough = requestview.Completion(req, rc.Request)

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := provider.TextCompletion(ctx, req)
		if err != nil {
			return err
		}
		nativeResp = resp
		rc.Response = chatAnswerFromCompletion(resp)
		rc.ResolvedModel = resp.Model
		return nil
	}

	// An output check that withheld the answer is refused like a pre-stage
	// block, and nativeResp is never served.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if nativeResp == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(nativeResp)
}

// chatAnswerFromCompletion is a native /v1/completions answer as the chat
// answer rc.Response holds: one choice per choice of the answer, with its
// index, its text as the message content and its finish reason, and the
// answer's usage and model.
func chatAnswerFromCompletion(resp *models.CompletionResponse) *models.ChatCompletionResponse {
	answer := &models.ChatCompletionResponse{
		ID:                resp.ID,
		Object:            "chat.completion",
		Created:           resp.Created,
		Model:             resp.Model,
		Choices:           make([]models.Choice, 0, len(resp.Choices)),
		Usage:             resp.Usage,
		SystemFingerprint: resp.SystemFingerprint,
	}
	for _, choice := range resp.Choices {
		content, _ := json.Marshal(choice.Text)
		answer.Choices = append(answer.Choices, models.Choice{
			Index:        choice.Index,
			Message:      models.Message{Role: "assistant", Content: content},
			FinishReason: choice.FinishReason,
		})
	}
	return answer
}

func (h *Handlers) handleCompletionNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider) {
	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := provider.ChatCompletion(ctx, rc.Request)
		if err != nil {
			return err
		}
		rc.Response = resp
		rc.ResolvedModel = resp.Model
		return nil
	}

	// An output check that withheld the answer is refused like a pre-stage
	// block, not answered 500 for the response it dropped.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.Response == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	// Convert chat response to legacy completion format.
	completionResp := models.CompletionResponseFromChat(rc.Response)

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(completionResp)
}

func (h *Handlers) handleCompletionStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider) {
	sseWriter := streaming.NewSSEWriter(w)
	if sseWriter == nil {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}

	h.setAgentccHeaders(w, rc)

	var chunks <-chan models.StreamChunk
	var errCh <-chan error

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		chunks, errCh = provider.StreamChatCompletion(ctx, rc.Request)
		return nil
	}

	// A refusal keeps its own status (a guardrail block 403, a missing key
	// 401), as on every other surface. A cache hit an output check withheld is
	// refused the same way.
	if err := pipelineErr(rc, h.engine.Process(ctx, rc, providerCall)); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		completionResp := models.CompletionResponseFromChat(rc.Response)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(completionResp)
		return
	}

	if chunks == nil {
		models.WriteError(w, models.ErrInternal("no stream from provider"))
		return
	}

	sseWriter.WriteHeaders()

	// Track last usage from stream chunks for post-plugin cost/credits tracking.
	var lastUsage *models.Usage
	// The stream's id and created, for the final usage chunk.
	var streamID string
	var streamCreated int64
	// The answer the stream sends, assembled for the request log and for an
	// output check that reads it at the stream's end, whether or not the log
	// keeps bodies.
	capture := newStreamCapture(h.captureStreamContent || rc.Flags.AnswerReaders != 0)

	// finalizeStream populates rc.Response with accumulated usage and runs
	// post-plugins (cost, credits, logging). Must be called before every return.
	// It returns the usage the client sees, the one billed (nil when the
	// upstream sent none).
	finalizeStream := func(detach bool) *models.Usage {
		rc.Response = &models.ChatCompletionResponse{
			Object: "chat.completion",
			Model:  rc.ResolvedModel,
			Usage:  lastUsage,
		}
		capture.applyTo(rc.Response)
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
		return billedUsage(rc, lastUsage)
	}

	// writeChunk writes a chat chunk to the client as a legacy completion
	// chunk.
	writeChunk := func(chunk models.StreamChunk) error {
		data, err := json.Marshal(models.CompletionStreamChunkFromChat(chunk))
		if err != nil {
			slog.Warn("error marshaling completion stream chunk",
				"request_id", rc.RequestID,
				"error", err,
			)
			return nil
		}
		return sseWriter.WriteRaw(data)
	}
	// For a client that did not ask for usage, each choice's finish chunk
	// waits for the upstream's end and goes out with the billed usage
	// (finishHold), as on a chat stream.
	hold := newFinishHold(rc)
	writeHeld := func(usage *models.Usage) {
		for _, c := range hold.drain(usage) {
			if err := writeChunk(c); err != nil {
				slog.Warn("error writing held completion finish chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				return
			}
		}
	}

	for {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				// The post-plugins run before [DONE], as on a chat stream, so
				// an output check that refuses the answer the stream sent (the
				// request log assembles it when it keeps bodies) ends the
				// stream with the guardrail's error event.
				usage := finalizeStream(false)
				writeHeld(usage)
				if apiErr := withheldAnswerError(rc); apiErr != nil {
					sseWriter.WriteError(apiErr)
				}
				// A client that asked for usage, or for the gateway's
				// metadata, gets it once, right before [DONE], with empty
				// choices (OpenAI's streaming contract).
				if wantsFinalStreamChunk(rc) {
					final := models.CompletionStreamChunkFromChat(*h.buildStreamingMetadataChunk(rc, streamID, streamCreated, lastUsage))
					if data, err := json.Marshal(final); err == nil {
						if err := sseWriter.WriteRaw(data); err != nil {
							slog.Warn("error writing final completion usage chunk",
								"request_id", rc.RequestID,
								"error", err,
							)
						}
					}
				}
				sseWriter.WriteDone()
				return
			}
			if streamID == "" && chunk.ID != "" {
				streamID = chunk.ID
			}
			if streamCreated == 0 && chunk.Created != 0 {
				streamCreated = chunk.Created
			}
			if len(chunk.Choices) > 0 && rc.ResolvedModel == "" {
				rc.ResolvedModel = chunk.Model
			}
			// A provider's usage is metered here and never forwarded, on
			// whatever chunk it rides (as on a chat stream): the client gets
			// the billed usage once, on the final chunk when it asked, else
			// on the held finish chunk.
			if chunk.Usage != nil {
				lastUsage = chunk.Usage
				chunk.Usage = nil
			}
			capture.observe(chunk)

			// A chunk with no choice (an upstream's usage chunk) is metered
			// above and not sent.
			if !clientGetsStreamChunk(chunk) {
				continue
			}

			release, held := hold.take(chunk)
			var err error
			for _, c := range release {
				if err = writeChunk(c); err != nil {
					break
				}
			}
			if err == nil && !held {
				err = writeChunk(chunk)
			}
			if err != nil {
				slog.Warn("error writing completion stream chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				finalizeStream(false)
				return
			}

		case err, ok := <-errCh:
			if ok && err != nil {
				apiErr, isAPI := err.(*models.APIError)
				if !isAPI {
					apiErr = models.ErrInternal(err.Error())
				}
				// A held finish chunk goes out before the error event, in
				// its place in the stream, with the billed usage.
				if !hold.pending() {
					sseWriter.WriteError(apiErr)
					finalizeStream(false)
					return
				}
				writeHeld(finalizeStream(false))
				sseWriter.WriteError(apiErr)
				return
			}

		case <-ctx.Done():
			// The request's context ended (the client disconnected, or a
			// server-side timeout fired): drain the channel and run the
			// post-plugins, then write a held finish chunk, best effort, as on
			// a chat stream.
			go func() {
				for range chunks {
				}
			}()
			writeHeld(finalizeStream(true))
			return
		}
	}
}
