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

	// Extract client IP for audit.
	rc.Metadata["client_ip"] = extractClientIP(r)

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
	if v := r.Header.Get("X-Guardrail-Policy"); v != "" {
		rc.Metadata["x-guardrail-policy"] = v
	}

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
	if h.healthMonitor != nil && rc.Provider != "" {
		if len(rc.Errors) > 0 {
			h.healthMonitor.RecordError(rc.Provider, rc.Errors[0])
		} else {
			h.healthMonitor.RecordSuccess(rc.Provider)
		}
	}

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
// completion vs chat format. The assistant message content mirrors the first
// choice's text so logs remain human-readable.
func (h *Handlers) handleCompletionNative(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider nativeTextCompletionProvider, req *models.CompletionRequest) {
	var nativeResp *models.CompletionResponse

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := provider.TextCompletion(ctx, req)
		if err != nil {
			return err
		}
		nativeResp = resp
		text := ""
		if len(resp.Choices) > 0 {
			text = resp.Choices[0].Text
		}
		contentJSON, _ := json.Marshal(text)
		finish := ""
		if len(resp.Choices) > 0 {
			finish = resp.Choices[0].FinishReason
		}
		rc.Response = &models.ChatCompletionResponse{
			ID:      resp.ID,
			Object:  "chat.completion",
			Created: resp.Created,
			Model:   resp.Model,
			Choices: []models.Choice{{
				Index:        0,
				Message:      models.Message{Role: "assistant", Content: contentJSON},
				FinishReason: finish,
			}},
			Usage:             resp.Usage,
			SystemFingerprint: resp.SystemFingerprint,
		}
		rc.ResolvedModel = resp.Model
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
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

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
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

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		// A pre-plugin's refusal — auth, rate limit, budget — keeps its status.
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

	// Track last usage from stream chunks for post-plugin cost/credits tracking.
	var lastUsage *models.Usage
	capture := newStreamCapture(h.captureStreamContent)

	// finalizeStream populates rc.Response with accumulated usage and runs
	// post-plugins (cost, credits, logging). Must be called before every return.
	finalizeStream := func(detach bool) {
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
	}

	// The provider asks for usage on every stream, so the cost is known; the
	// chunk that carries it goes to the client only if the client asked too.
	clientWantsUsage := rc.Request.StreamOptions != nil && rc.Request.StreamOptions.IncludeUsage

	// relay sends one chunk on as a legacy completion chunk; false ends the stream.
	relay := func(chunk models.StreamChunk) bool {
		if len(chunk.Choices) > 0 && rc.ResolvedModel == "" {
			rc.ResolvedModel = chunk.Model
		}
		if chunk.Usage != nil {
			lastUsage = chunk.Usage
		}
		capture.observe(chunk)
		if len(chunk.Choices) == 0 && chunk.Usage != nil && !clientWantsUsage {
			return true
		}

		data, err := json.Marshal(models.CompletionStreamChunkFromChat(chunk))
		if err != nil {
			slog.Warn("error marshaling completion stream chunk",
				"request_id", rc.RequestID,
				"error", err,
			)
			return true
		}
		if err := sseWriter.WriteRaw(data); err != nil {
			slog.Warn("error writing completion stream chunk",
				"request_id", rc.RequestID,
				"error", err,
			)
			finalizeStream(false)
			return false
		}
		return true
	}

	// Commit to the 200 only once the upstream has produced a chunk, so a
	// stream that fails to start — a provider's 4xx included — answers with
	// its own status, as /v1/chat/completions does.
	first, startErr := waitForFirstStreamChunk(ctx, chunks, errCh)
	if startErr != nil {
		if apiErr := streamEndError(ctx, startErr); apiErr != nil {
			rc.AddError(apiErr)
			models.WriteError(w, apiErr)
		}
		finalizeStream(ctx.Err() != nil)
		return
	}
	sseWriter.WriteHeaders()
	if !relay(*first) {
		return
	}

	for {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				if apiErr, finished := closedStreamError(ctx, errCh); !finished {
					if apiErr != nil {
						rc.AddError(apiErr)
						sseWriter.WriteError(apiErr)
					}
					finalizeStream(ctx.Err() != nil)
					return
				}
				if wantsStreamMetadata(rc) {
					// Finalise first: the last chunk carries the cost.
					finalizeStream(false)
					metadataChunk := models.CompletionStreamChunkFromChat(*h.buildStreamingMetadataChunk(rc, "", 0, lastUsage))
					if data, err := json.Marshal(metadataChunk); err == nil {
						sseWriter.WriteRaw(data)
					}
					sseWriter.WriteDone()
					return
				}
				sseWriter.WriteDone()
				finalizeStream(false)
				return
			}
			if !relay(chunk) {
				return
			}

		case err, ok := <-errCh:
			if ok && err != nil {
				apiErr, isAPI := err.(*models.APIError)
				if !isAPI {
					apiErr = models.ErrInternal(err.Error())
				}
				rc.AddError(apiErr)
				sseWriter.WriteError(apiErr)
				finalizeStream(false)
				return
			}

		case <-ctx.Done():
			// The client left, or the request reached its deadline; only the
			// second has anyone to tell. Drain the channel and run post-plugins.
			if apiErr := streamEndError(ctx, ctx.Err()); apiErr != nil {
				rc.AddError(apiErr)
				sseWriter.WriteError(apiErr)
			}
			go func() {
				for range chunks {
				}
			}()
			finalizeStream(true)
			return
		}
	}
}
