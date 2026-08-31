package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
	geminitrans "github.com/futureagi/agentcc-gateway/internal/translation/gemini"
)

// GenAIHandler handles POST /v1beta/models/{model_action} — native Google GenAI API pass-through.
func (h *Handlers) GenAIHandler(w http.ResponseWriter, r *http.Request) {
	modelAction := r.URL.Query().Get("model_action")
	if modelAction == "" {
		genaifmt.WriteError(w, http.StatusBadRequest, "INVALID_ARGUMENT", "missing model and action in path")
		return
	}

	model, action, err := genaifmt.ExtractModelFromPath(modelAction)
	if err != nil {
		genaifmt.WriteError(w, http.StatusBadRequest, "INVALID_ARGUMENT", err.Error())
		return
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.Model = model
	rc.RequestHeaders = cloneRequestHeaders(r)

	switch action {
	case "generateContent":
		rc.EndpointType = "genai_generate"
		rc.IsStream = false
	case "streamGenerateContent":
		rc.EndpointType = "genai_stream"
		rc.IsStream = true
	case "countTokens":
		rc.EndpointType = "genai_count_tokens"
		rc.IsStream = false
	case "embedContent":
		rc.EndpointType = "genai_embed"
		rc.IsStream = false
	}

	rc.Metadata["client_ip"] = extractClientIP(r)

	// Extract auth from query param, header, or Authorization header.
	if key := r.URL.Query().Get("key"); key != "" {
		rc.Metadata["authorization"] = "Bearer " + key
		// Strip key from URL to avoid leaking in logs.
		q := r.URL.Query()
		q.Del("key")
		r.URL.RawQuery = q.Encode()
	} else if key := r.Header.Get("x-goog-api-key"); key != "" {
		rc.Metadata["authorization"] = "Bearer " + key
	} else if authHeader := r.Header.Get("Authorization"); authHeader != "" {
		rc.Metadata["authorization"] = authHeader
	}

	// Extract agentcc metadata.
	if meta := r.Header.Get("x-agentcc-metadata"); meta != "" {
		parseMetadataHeader(meta, rc)
	}

	// Read request body.
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		genaifmt.WriteError(w, http.StatusBadRequest, "INVALID_ARGUMENT", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBodySize {
		genaifmt.WriteError(w, http.StatusRequestEntityTooLarge, "INVALID_ARGUMENT",
			fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize))
		return
	}

	// Caller dimensions from the body's own non-spec fields. Read from the raw
	// bytes: the native pass-through below never unmarshals this body.
	applyCallerExtrasFromBody(rc, body, geminitrans.KnownRequestFields())

	// Resolve timeout and context.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	// Resolve org config.
	h.peekKeyOrgID(rc)
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

	provider, err := h.resolveProviderWithOrgFallback(ctx, rc, orgID, orgCfg, model)
	if err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	// Collect Google-specific headers to forward (only used on native path).
	googleHeaders := make(map[string]string)
	for key := range r.Header {
		if len(key) > 6 && key[:7] == "X-Goog-" || len(key) > 6 && key[:7] == "x-goog-" {
			googleHeaders[key] = r.Header.Get(key)
		}
	}

	// embedContent and countTokens are native-only (no canonical equivalent).
	// Route them to the native provider, or 501 if not available.
	if action == "embedContent" || action == "countTokens" {
		gp, ok := provider.(providers.GenAINativeProvider)
		if !ok {
			if action == "embedContent" {
				genaifmt.WriteError(w, http.StatusNotFound, "NOT_FOUND",
					fmt.Sprintf("Provider %q does not support native GenAI embedContent. "+
						"Re-register with api_format=\"google\".", rc.Provider))
			} else {
				// countTokens — 501 for non-native backends per spec.
				h.setAgentccHeaders(w, rc)
				genaifmt.WriteError(w, http.StatusNotImplemented, "UNIMPLEMENTED",
					fmt.Sprintf("countTokens is not supported for provider %q (api_format is not google). "+
						"Token counts are in the usageMetadata block of generateContent responses.", rc.Provider))
			}
			return
		}
		if action == "embedContent" {
			h.handleGenAIEmbed(ctx, w, rc, gp, model, body, googleHeaders)
		} else {
			h.handleGenAICountTokens(ctx, w, rc, gp, model, body, googleHeaders)
		}
		return
	}

	// Fast path: provider natively speaks GenAI.
	if gp, ok := provider.(providers.GenAINativeProvider); ok {
		switch action {
		case "generateContent":
			h.handleGenAINonStream(ctx, w, rc, gp, model, body, googleHeaders)
		case "streamGenerateContent":
			h.handleGenAIStream(ctx, w, rc, gp, model, body, googleHeaders)
		}
		return
	}

	// Translation path: provider speaks OpenAI-canonical internally.
	translator, ok := translation.InboundFor("google")
	if !ok {
		genaifmt.WriteError(w, http.StatusInternalServerError, "INTERNAL", "google translator not registered")
		return
	}

	canonicalReq, drops, err := translator.RequestToCanonical(body)
	if err != nil {
		status, errBody, ct := translator.ErrorFromCanonical(&models.APIError{
			Status:  http.StatusBadRequest,
			Type:    models.ErrTypeInvalidRequest,
			Message: err.Error(),
		})
		w.Header().Set("Content-Type", ct)
		w.WriteHeader(status)
		w.Write(errBody)
		return
	}
	if len(drops) > 0 {
		joined := strings.Join(drops, ",")
		rc.Metadata["translation_drops"] = joined
		w.Header().Set("x-agentcc-translation-drops", joined)
	}
	canonicalReq.Model = rc.Model
	canonicalReq.Stream = action == "streamGenerateContent"

	switch action {
	case "generateContent":
		h.handleGenAINonStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq)
	case "streamGenerateContent":
		h.handleGenAIStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq)
	}
}

// ─── Native pass-through helpers ─────────────────────────────────────────────

func (h *Handlers) handleGenAINonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, gp providers.GenAINativeProvider, model string, reqBody []byte, headers map[string]string) {
	var respBody []byte
	var statusCode int

	err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		respBody, statusCode, callErr = gp.GenerateContent(ctx, model, reqBody, headers)
		if callErr != nil {
			return callErr
		}
		if statusCode < 400 {
			applyGenAIUsage(rc, respBody)
		}
		return nil
	})
	if err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	// A plugin answered instead of the provider — a cache hit.
	if respBody == nil {
		h.writeGenAIShortCircuit(w, rc)
		return
	}

	// If upstream returned an error, forward as-is.
	if statusCode >= 400 {
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(respBody)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleGenAIStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, gp providers.GenAINativeProvider, model string, reqBody []byte, headers map[string]string) {
	var stream io.ReadCloser
	var statusCode int

	// Post-plugins wait for the closing chunk, where the counts arrive.
	if err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		stream, statusCode, callErr = gp.StreamGenerateContent(ctx, model, reqBody, headers)
		return callErr
	}); err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		h.writeGenAIShortCircuit(w, rc)
		return
	}
	if stream == nil {
		writeGenAIErrorFromError(w, models.ErrInternal("no stream from provider"))
		return
	}
	defer stream.Close()

	var usage genaifmt.StreamUsageScanner
	finalize := func(detach bool) {
		applyGenAIStreamUsage(rc, &usage)
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
	}

	// If upstream returned an error status, read and forward. Still finalised:
	// the non-streaming path records a 4xx through Process, and a streamed one
	// that skipped it would be missing from the log and the trace.
	if statusCode >= 400 {
		errBody, _ := io.ReadAll(io.LimitReader(stream, 1024*1024))
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(errBody)
		finalize(false)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported", "request_id", rc.RequestID)
		// Stream is open, so the request still has to be accounted for.
		finalize(true)
		return
	}
	flusher.Flush()

	// Relay SSE stream from GenAI to client.
	buf := make([]byte, 32*1024)
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			usage.Write(buf[:n])
			if _, writeErr := w.Write(buf[:n]); writeErr != nil {
				slog.Warn("error writing genai stream", "request_id", rc.RequestID, "error", writeErr)
				// Client gone, tokens still spent — detach from its context.
				finalize(true)
				return
			}
			flusher.Flush()
		}
		if readErr != nil {
			if readErr != io.EOF {
				slog.Warn("error reading genai stream", "request_id", rc.RequestID, "error", readErr)
			}
			finalize(false)
			return
		}

		select {
		case <-ctx.Done():
			finalize(true)
			return
		default:
		}
	}
}

// applyGenAIUsage lifts usageMetadata onto rc. rc.Response is populated because
// that is what the cost plugin prices from; it carries usage only.
func applyGenAIUsage(rc *models.RequestContext, respBody []byte) {
	promptTokens, completionTokens, _ := genaifmt.ExtractUsageMetadata(respBody)
	if model := genaifmt.ExtractModelVersion(respBody); model != "" {
		rc.ResolvedModel = model
	}
	if promptTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(promptTokens)
	}
	if completionTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(completionTokens)
	}
	if promptTokens == 0 && completionTokens == 0 {
		return
	}
	setUsageResponse(rc, promptTokens, completionTokens)
}

// applyGenAIStreamUsage moves collected counts onto rc. Left nil when the
// stream reported none, so the cost plugin skips rather than bills zero.
func applyGenAIStreamUsage(rc *models.RequestContext, usage *genaifmt.StreamUsageScanner) {
	if model := usage.Model(); model != "" {
		rc.ResolvedModel = model
	}
	promptTokens, completionTokens, ok := usage.Usage()
	if !ok {
		return
	}
	if promptTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(promptTokens)
	}
	if completionTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(completionTokens)
	}
	setUsageResponse(rc, promptTokens, completionTokens)
}

// writeGenAIShortCircuit renders a plugin-produced response in GenAI format.
func (h *Handlers) writeGenAIShortCircuit(w http.ResponseWriter, rc *models.RequestContext) {
	translator, ok := translation.InboundFor("google")
	if !ok || rc.Response == nil {
		h.setAgentccHeaders(w, rc)
		genaifmt.WriteError(w, http.StatusInternalServerError, "INTERNAL",
			"request was answered internally but the response could not be formatted")
		return
	}
	out, err := translator.ResponseFromCanonical(rc.Response)
	if err != nil {
		h.setAgentccHeaders(w, rc)
		genaifmt.WriteError(w, http.StatusInternalServerError, "INTERNAL",
			"failed to format response: "+err.Error())
		return
	}
	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(out)
}

func (h *Handlers) handleGenAICountTokens(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, gp providers.GenAINativeProvider, model string, reqBody []byte, headers map[string]string) {
	respBody, statusCode, err := gp.GenAICountTokens(ctx, model, reqBody, headers)
	if err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	if statusCode >= 400 {
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(respBody)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleGenAIEmbed(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, gp providers.GenAINativeProvider, model string, reqBody []byte, headers map[string]string) {
	respBody, statusCode, err := gp.EmbedContent(ctx, model, reqBody, headers)
	if err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	if statusCode >= 400 {
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(respBody)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

// ─── Translation-path helpers ─────────────────────────────────────────────────

// handleGenAINonStreamViaCanonical routes a non-streaming Gemini
// generateContent request through the canonical provider interface.
func (h *Handlers) handleGenAINonStreamViaCanonical(
	ctx context.Context,
	w http.ResponseWriter,
	rc *models.RequestContext,
	provider providers.Provider,
	translator translation.InboundTranslator,
	canonicalReq *models.ChatCompletionRequest,
) {
	// Without this the pipeline sees a nil rc.Request.
	rc.Request = canonicalReq

	var canonicalResp *models.ChatCompletionResponse
	err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		canonicalResp, callErr = provider.ChatCompletion(ctx, canonicalReq)
		if callErr != nil {
			return callErr
		}
		rc.Response = canonicalResp
		return nil
	})
	if err != nil {
		var apiErr *models.APIError
		if errors.As(err, &apiErr) {
			status, body, ct := translator.ErrorFromCanonical(apiErr)
			h.setAgentccHeaders(w, rc)
			w.Header().Set("Content-Type", ct)
			w.WriteHeader(status)
			w.Write(body)
			return
		}
		status, body, ct := translator.ErrorFromCanonical(&models.APIError{
			Status:  http.StatusBadGateway,
			Type:    models.ErrTypeServer,
			Message: err.Error(),
		})
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", ct)
		w.WriteHeader(status)
		w.Write(body)
		return
	}

	// A plugin answered instead of the provider — a cache hit.
	if canonicalResp == nil {
		canonicalResp = rc.Response
	}
	if canonicalResp == nil {
		status, errBody, ct := translator.ErrorFromCanonical(&models.APIError{
			Status:  http.StatusInternalServerError,
			Type:    models.ErrTypeServer,
			Message: "no response from provider",
		})
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", ct)
		w.WriteHeader(status)
		w.Write(errBody)
		return
	}

	// Track usage.
	if canonicalResp.Usage != nil {
		if canonicalResp.Usage.PromptTokens > 0 {
			rc.Metadata["input_tokens"] = strconv.Itoa(canonicalResp.Usage.PromptTokens)
		}
		if canonicalResp.Usage.CompletionTokens > 0 {
			rc.Metadata["output_tokens"] = strconv.Itoa(canonicalResp.Usage.CompletionTokens)
		}
	}
	if canonicalResp.Model != "" {
		rc.ResolvedModel = canonicalResp.Model
	}

	respBody, marshalErr := translator.ResponseFromCanonical(canonicalResp)
	if marshalErr != nil {
		status, errBody, ct := translator.ErrorFromCanonical(&models.APIError{
			Status:  http.StatusInternalServerError,
			Type:    models.ErrTypeServer,
			Message: "failed to format response: " + marshalErr.Error(),
		})
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", ct)
		w.WriteHeader(status)
		w.Write(errBody)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

// handleGenAIStreamViaCanonical routes a streaming Gemini
// streamGenerateContent request through the canonical provider interface,
// piping chunks through the translator's SSE state machine.
func (h *Handlers) handleGenAIStreamViaCanonical(
	ctx context.Context,
	w http.ResponseWriter,
	rc *models.RequestContext,
	provider providers.Provider,
	translator translation.InboundTranslator,
	canonicalReq *models.ChatCompletionRequest,
) {
	rc.Request = canonicalReq

	var rawChunkCh <-chan models.StreamChunk
	var errCh <-chan error

	if err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		rawChunkCh, errCh = provider.StreamChatCompletion(ctx, canonicalReq)
		return nil
	}); err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		h.writeGenAIShortCircuit(w, rc)
		return
	}
	if rawChunkCh == nil {
		writeGenAIErrorFromError(w, models.ErrInternal("no stream from provider"))
		return
	}

	// Tee for usage from the final chunk.
	chunkCh, usageCh := teeStreamUsage(ctx, rawChunkCh)

	finalize := func(detach bool) {
		applyCanonicalStreamUsage(rc, usageCh)
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
	}

	// Translate OpenAI chunks into Gemini SSE events.
	// Gemini's translator uses blocking sends (cap 32); the consumer here drives
	// the drain so backpressure is handled naturally.
	eventCh, translatorErrCh := translator.StreamEventsFromCanonical(ctx, chunkCh)

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported", "request_id", rc.RequestID)
		// Stream is open, so the request still has to be accounted for.
		finalize(true)
		return
	}
	flusher.Flush()

	for {
		select {
		case <-ctx.Done():
			finalize(true)
			return

		case event, ok := <-eventCh:
			if !ok {
				eventCh = nil
				continue
			}
			if _, writeErr := w.Write(event); writeErr != nil {
				slog.Warn("error writing translated genai stream", "request_id", rc.RequestID, "error", writeErr)
				finalize(true)
				return
			}
			flusher.Flush()

		case err, ok := <-translatorErrCh:
			if !ok {
				// Error channel closed. Keep draining eventCh — final events
				// (finishReason payload, usageMetadata) may still be buffered.
				translatorErrCh = nil
				continue
			}
			if err != nil {
				slog.Warn("translator stream error", "request_id", rc.RequestID, "error", err)
				finalize(false)
				return
			}

		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				continue
			}
			if err != nil {
				slog.Warn("provider stream error", "request_id", rc.RequestID, "error", err)
				finalize(false)
				return
			}
		}

		if eventCh == nil && errCh == nil && translatorErrCh == nil {
			finalize(false)
			return
		}
	}
}

// writeGenAIErrorFromError converts an error to Google GenAI error format.
func writeGenAIErrorFromError(w http.ResponseWriter, err error) {
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		genaifmt.WriteAPIError(w, apiErr)
		return
	}
	genaifmt.WriteError(w, http.StatusInternalServerError, "INTERNAL", err.Error())
}
