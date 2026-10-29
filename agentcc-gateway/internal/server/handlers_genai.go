package server

import (
	"bytes"
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
	"github.com/futureagi/agentcc-gateway/internal/streaming"
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
	// Retain provider-qualified routing identity, but send only the model name
	// upstream on the native Google API (e.g. vertex_ai/gemini-3.8-flash).
	if prefix := r.URL.Query().Get("provider"); prefix != "" {
		rc.Model = prefix + "/" + model
	}
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

	provider, upstreamModel, err := h.resolveProviderWithOrgFallback(ctx, rc, orgID, orgCfg, rc.Model)
	if err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}
	if upstreamModel != rc.Model {
		model = providers.ResolveModelName(upstreamModel)
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
		call := func(ctx context.Context) ([]byte, int, error) {
			return gp.GenAICountTokens(ctx, model, body, googleHeaders)
		}
		if action == "embedContent" {
			call = func(ctx context.Context) ([]byte, int, error) {
				respBody, status, err := gp.EmbedContent(ctx, model, body, googleHeaders)
				if err == nil && status < http.StatusBadRequest {
					// Only an embedding that was made is billed.
					rc.Metadata["input_characters"] = strconv.Itoa(genaifmt.EmbedInputCharacters(body))
				}
				return respBody, status, err
			}
		}
		h.handleGenAINativeOnly(ctx, w, rc, call)
		return
	}

	// Fast path: provider natively speaks GenAI.
	if gp, ok := provider.(providers.GenAINativeProvider); ok {
		rc.GuardrailRequest = guardrailRequest("google", body)
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
	canonicalReq.Model = upstreamModel
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
			rc.GuardrailResponse = assistantText(genaiResponseText(respBody))
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

	// With metadata asked for, the latest chunk is held back to carry it.
	var holder *lastEventHolder
	if wantsStreamMetadata(rc) {
		holder = &lastEventHolder{}
	}

	// fail ends a stream that stopped early, telling the client why unless
	// the client is the one that left.
	fail := func(err error) {
		if holder != nil {
			writeAndFlush(w, flusher, holder.flush(nil))
		}
		if apiErr := streamEndError(ctx, err); apiErr != nil {
			writeGenAIStreamError(w, flusher, rc, apiErr)
		}
		finalize(ctx.Err() != nil)
	}

	// Relay the stream one event at a time, each as it arrived, so a failure
	// is reported after a whole event rather than inside one.
	events := streaming.NewEventReader(stream)
	for {
		ev, readErr := events.Next()
		usage.Write(ev.Raw)
		if readErr != nil && readErr != io.EOF {
			// The event the error cut off is not forwarded: run into the error
			// chunk, it would hide that chunk from the client's parser.
			slog.Warn("error reading genai stream", "request_id", rc.RequestID, "error", readErr)
			fail(readErr)
			return
		}
		if apiErr := genaiStreamError(ev.Raw); apiErr != nil {
			rc.AddError(apiErr)
		}
		out := ev.Raw
		if holder != nil {
			out = holder.push(ev)
		}
		if len(out) > 0 {
			if _, writeErr := w.Write(out); writeErr != nil {
				slog.Warn("error writing genai stream", "request_id", rc.RequestID, "error", writeErr)
				// Client gone, tokens still spent — detach from its context.
				finalize(true)
				return
			}
			flusher.Flush()
		}
		switch {
		case readErr == io.EOF:
			finalize(false)
			if holder != nil {
				writeAndFlush(w, flusher, holder.flush(streamMetadata(rc)))
			}
			return
		case ctx.Err() != nil:
			fail(ctx.Err())
			return
		}
	}
}

// writeAndFlush sends bytes a stream has already committed to, if there are
// any. A failed write needs no handling here: the client is gone, and the
// caller is ending the stream either way.
func writeAndFlush(w io.Writer, flusher http.Flusher, out []byte) {
	if len(out) == 0 {
		return
	}
	if _, err := w.Write(out); err == nil {
		flusher.Flush()
	}
}

// writeGenAIStreamError reports a failure inside a stream that has already
// answered 200, and records it, so the request is logged as the failure it was.
func writeGenAIStreamError(w http.ResponseWriter, flusher http.Flusher, rc *models.RequestContext, apiErr *models.APIError) {
	rc.AddError(apiErr)
	if _, err := w.Write(genaifmt.StreamErrorEvent(apiErr)); err == nil {
		flusher.Flush()
	}
}

// genaiStreamError is the failure a GenAI stream event reports, or nil. A
// GenAI stream has no error event; a failing upstream, or the translator,
// sends an error object in place of a chunk.
func genaiStreamError(raw []byte) *models.APIError {
	if !bytes.Contains(raw, []byte(`"error"`)) {
		return nil
	}
	ev, _ := streaming.NewEventReader(bytes.NewReader(raw)).Next()
	data := bytes.TrimSpace(ev.Data())
	if !bytes.HasPrefix(data, []byte(`{"error"`)) {
		return nil
	}
	return upstreamStreamError(data)
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

// handleGenAINativeOnly serves countTokens and embedContent. They have no
// canonical form, but still go through the pipeline: without it the key is
// never authenticated, and the call is neither rate-limited nor logged.
func (h *Handlers) handleGenAINativeOnly(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, call func(context.Context) ([]byte, int, error)) {
	var respBody []byte
	var statusCode int
	if err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		respBody, statusCode, callErr = call(ctx)
		if callErr == nil && statusCode >= http.StatusBadRequest {
			// Relayed as sent, and logged as the failure it is.
			rc.AddError(models.ErrUpstreamProvider(statusCode, fmt.Sprintf("genai error (HTTP %d)", statusCode)))
		}
		return callErr
	}); err != nil {
		writeGenAIErrorFromError(w, err)
		return
	}
	if respBody == nil {
		writeGenAIErrorFromError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	if statusCode < 400 {
		statusCode = http.StatusOK
	}
	w.WriteHeader(statusCode)
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
		canonicalResp, callErr = translatedChatCompletion(ctx, provider, canonicalReq)
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
		rawChunkCh, errCh = translatedStreamChatCompletion(ctx, provider, canonicalReq)
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

	// With metadata asked for, the latest chunk is held back to carry it.
	// release sends it as it came, before an error ends the stream.
	var holder *lastEventHolder
	if wantsStreamMetadata(rc) {
		holder = &lastEventHolder{}
	}
	release := func() {
		if holder != nil {
			writeAndFlush(w, flusher, holder.flush(nil))
		}
	}

	for {
		select {
		case <-ctx.Done():
			release()
			if apiErr := streamEndError(ctx, ctx.Err()); apiErr != nil {
				writeGenAIStreamError(w, flusher, rc, apiErr)
			}
			finalize(true)
			return

		case event, ok := <-eventCh:
			if !ok {
				// break, not continue: the check after the select is what
				// ends the stream once every channel has closed.
				eventCh = nil
				break
			}
			if apiErr := genaiStreamError(event); apiErr != nil {
				rc.AddError(apiErr)
			}
			if holder != nil {
				ev, _ := streaming.NewEventReader(bytes.NewReader(event)).Next()
				if event = holder.push(ev); len(event) == 0 {
					break
				}
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
				break
			}
			if err != nil {
				slog.Warn("translator stream error", "request_id", rc.RequestID, "error", err)
				release()
				writeGenAIStreamError(w, flusher, rc, models.ErrUpstreamProvider(0, "translating the stream: "+err.Error()))
				finalize(false)
				return
			}

		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				break
			}
			if err != nil {
				slog.Warn("provider stream error", "request_id", rc.RequestID, "error", err)
				release()
				if apiErr := streamEndError(ctx, err); apiErr != nil {
					writeGenAIStreamError(w, flusher, rc, apiErr)
				}
				finalize(false)
				return
			}
		}

		if eventCh == nil && errCh == nil && translatorErrCh == nil {
			// Every channel closed. The deadline closes them too, and the
			// providers report nothing once it has passed.
			if apiErr, finished := closedStreamError(ctx, nil); !finished {
				release()
				if apiErr != nil {
					writeGenAIStreamError(w, flusher, rc, apiErr)
				}
				finalize(ctx.Err() != nil)
				return
			}
			finalize(false)
			if holder != nil {
				writeAndFlush(w, flusher, holder.flush(streamMetadata(rc)))
			}
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
