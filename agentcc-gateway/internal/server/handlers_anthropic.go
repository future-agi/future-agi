package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
	_ "github.com/futureagi/agentcc-gateway/internal/translation/anthropic" // register translator
)

// MappingRestorer is satisfied by translators that can restore truncated tool
// names when converting a response back to native format.  We type-assert to
// this interface when the canonical request carries a tool_name_mapping in its
// Extra; the plain ResponseFromCanonical is used otherwise.  This keeps the
// concept out of the generic InboundTranslator interface.
type MappingRestorer interface {
	ResponseFromCanonicalWithMapping(
		resp *models.ChatCompletionResponse,
		toolNameMapping map[string]string,
	) ([]byte, error)
}

// AnthropicMessages handles POST /v1/messages — native Anthropic Messages API pass-through.
func (h *Handlers) AnthropicMessages(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "anthropic_messages"
	rc.RequestHeaders = cloneRequestHeaders(r)
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBodySize {
		anthropicfmt.WriteError(w, http.StatusRequestEntityTooLarge, "invalid_request_error",
			fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize))
		return
	}

	// Extract minimal fields.
	model, isStream, hasMaxTokens, parseErr := anthropicfmt.ExtractMinimalRequest(body)
	if parseErr != nil {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON: "+parseErr.Error())
		return
	}
	if model == "" {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "model is required")
		return
	}
	if !hasMaxTokens {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "max_tokens is required")
		return
	}

	rc.Model = model
	rc.IsStream = isStream
	rc.Metadata["client_ip"] = extractClientIP(r)

	// Extract headers.
	setAuthMetadataFromRequest(rc, r)
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

	// Resolve timeout and context.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	// Peek key org_id before resolving org config (same as ChatCompletion).
	h.peekKeyOrgID(rc)

	// Resolve org config.
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
				if orgModelMatches(m, model, providerID) {
					orgProvider, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, provCfg.APIKey, provCfg)
					if err == nil {
						provider = orgProvider
						rc.Provider = providerID
						rc.Metadata["org_provider_model_match"] = model
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
		var err error
		provider, err = h.resolveProvider(ctx, rc, model)
		if err != nil {
			if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
				if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, model); orgP != nil {
					provider = orgP
					rc.Provider = providerID
					rc.Metadata["org_provider"] = "true"
					err = nil
				}
			}
			if err != nil {
				writeAnthropicErrorFromError(w, err)
				return
			}
		}
	}
	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Build Anthropic-specific headers to forward.
	anthropicHeaders := make(map[string]string)
	for _, key := range []string{"anthropic-version", "anthropic-beta", "anthropic-dangerous-direct-browser-access"} {
		if v := r.Header.Get(key); v != "" {
			anthropicHeaders[key] = v
		}
	}

	// Fast path: provider natively speaks Anthropic.
	if ap, ok := provider.(providers.AnthropicNativeProvider); ok {
		if isStream {
			h.handleAnthropicStream(ctx, w, rc, ap, body, anthropicHeaders)
		} else {
			h.handleAnthropicNonStream(ctx, w, rc, ap, body, anthropicHeaders)
		}
		return
	}

	// Translation path: provider speaks OpenAI-canonical internally.
	translator, ok := translation.InboundFor("anthropic")
	if !ok {
		// Should never happen — init() registers it.
		anthropicfmt.WriteError(w, http.StatusInternalServerError, "api_error", "anthropic translator not registered")
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
	canonicalReq.Stream = isStream

	if isStream {
		h.handleAnthropicStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq)
	} else {
		h.handleAnthropicNonStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq)
	}
}

// AnthropicCountTokens handles POST /v1/messages/count_tokens — native Anthropic count_tokens pass-through.
func (h *Handlers) AnthropicCountTokens(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "anthropic_count_tokens"
	rc.RequestHeaders = cloneRequestHeaders(r)
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBodySize {
		anthropicfmt.WriteError(w, http.StatusRequestEntityTooLarge, "invalid_request_error",
			fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize))
		return
	}

	var req struct {
		Model json.RawMessage `json:"model"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON: "+err.Error())
		return
	}
	var model string
	if err := json.Unmarshal(req.Model, &model); err != nil || model == "" {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "model is required")
		return
	}

	rc.Model = model
	rc.Metadata["client_ip"] = extractClientIP(r)
	setAuthMetadataFromRequest(rc, r)

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

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
		writeAnthropicErrorFromError(w, err)
		return
	}

	// Fast path: native Anthropic provider supports count_tokens.
	if ap, ok := provider.(providers.AnthropicNativeProvider); ok {
		anthropicHeaders := make(map[string]string)
		for _, key := range []string{"anthropic-version", "anthropic-beta", "anthropic-dangerous-direct-browser-access"} {
			if v := r.Header.Get(key); v != "" {
				anthropicHeaders[key] = v
			}
		}
		respBody, statusCode, err := ap.CountAnthropicTokens(ctx, body, anthropicHeaders)
		if err != nil {
			writeAnthropicErrorFromError(w, err)
			return
		}
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(respBody)
		return
	}

	// Translation path: count_tokens is not supported for non-Anthropic backends.
	// Token counts are available in the usage block of the actual /v1/messages response.
	h.setAgentccHeaders(w, rc)
	anthropicfmt.WriteError(w, http.StatusNotImplemented, "not_supported_error",
		fmt.Sprintf("count_tokens is not supported for provider %q (api_format is not anthropic). "+
			"Use /v1/messages — token counts are in the response usage block.", rc.Provider))
}

// ─── Native pass-through helpers (unchanged from original) ───────────────────

func (h *Handlers) handleAnthropicNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, ap providers.AnthropicNativeProvider, body []byte, headers map[string]string) {
	respBody, statusCode, err := ap.CreateAnthropicMessage(ctx, body, headers)
	if err != nil {
		writeAnthropicErrorFromError(w, err)
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

	// Extract usage for cost tracking.
	inputTokens, outputTokens := anthropicfmt.ExtractUsage(respBody)
	if inputTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(inputTokens)
	}
	if outputTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(outputTokens)
	}

	// Extract resolved model.
	if resolvedModel := anthropicfmt.ExtractModel(respBody); resolvedModel != "" {
		rc.ResolvedModel = resolvedModel
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

func (h *Handlers) handleAnthropicStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, ap providers.AnthropicNativeProvider, body []byte, headers map[string]string) {
	stream, statusCode, err := ap.StreamAnthropicMessage(ctx, body, headers)
	if err != nil {
		writeAnthropicErrorFromError(w, err)
		return
	}
	defer stream.Close()

	// If upstream returned an error status, read and forward.
	if statusCode >= 400 {
		errBody, _ := io.ReadAll(io.LimitReader(stream, 1024*1024))
		h.setAgentccHeaders(w, rc)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		w.Write(errBody)
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
		return
	}
	flusher.Flush()

	// Relay SSE stream from Anthropic to client.
	buf := make([]byte, 32*1024)
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			if _, writeErr := w.Write(buf[:n]); writeErr != nil {
				slog.Warn("error writing anthropic stream", "request_id", rc.RequestID, "error", writeErr)
				return
			}
			flusher.Flush()
		}
		if readErr != nil {
			if readErr != io.EOF {
				slog.Warn("error reading anthropic stream", "request_id", rc.RequestID, "error", readErr)
			}
			return
		}

		select {
		case <-ctx.Done():
			return
		default:
		}
	}
}

// ─── Translation-path helpers ─────────────────────────────────────────────────

// handleAnthropicNonStreamViaCanonical routes a non-streaming Anthropic
// request through the canonical provider interface.
func (h *Handlers) handleAnthropicNonStreamViaCanonical(
	ctx context.Context,
	w http.ResponseWriter,
	rc *models.RequestContext,
	provider providers.Provider,
	translator translation.InboundTranslator,
	canonicalReq *models.ChatCompletionRequest,
) {
	canonicalResp, err := provider.ChatCompletion(ctx, canonicalReq)
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

	// Convert response back to Anthropic wire format.
	// If the original request had a tool_name_mapping, restore truncated names.
	var respBody []byte
	var marshalErr error

	if mapping := extractToolNameMapping(canonicalReq); mapping != nil {
		if mr, ok := translator.(MappingRestorer); ok {
			respBody, marshalErr = mr.ResponseFromCanonicalWithMapping(canonicalResp, mapping)
		} else {
			respBody, marshalErr = translator.ResponseFromCanonical(canonicalResp)
		}
	} else {
		respBody, marshalErr = translator.ResponseFromCanonical(canonicalResp)
	}

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

// handleAnthropicStreamViaCanonical routes a streaming Anthropic request
// through the canonical provider interface, piping chunks through the
// translator's SSE state machine.
func (h *Handlers) handleAnthropicStreamViaCanonical(
	ctx context.Context,
	w http.ResponseWriter,
	rc *models.RequestContext,
	provider providers.Provider,
	translator translation.InboundTranslator,
	canonicalReq *models.ChatCompletionRequest,
) {
	chunkCh, errCh := provider.StreamChatCompletion(ctx, canonicalReq)

	// Translate the OpenAI chunk stream into Anthropic SSE events. If the
	// request came in with a tool_name_mapping (a tool was truncated to fit
	// OpenAI's 64-char limit during RequestToCanonical), use the variant that
	// restores original names on content_block_start tool_use events —
	// otherwise the client would see the truncated name in the stream.
	var eventCh <-chan []byte
	var translatorErrCh <-chan error
	if mapping := extractToolNameMapping(canonicalReq); mapping != nil {
		type streamMappingRestorer interface {
			StreamEventsFromCanonicalWithMapping(ctx context.Context, chunks <-chan models.StreamChunk, mapping map[string]string) (<-chan []byte, <-chan error)
		}
		if smr, ok := translator.(streamMappingRestorer); ok {
			eventCh, translatorErrCh = smr.StreamEventsFromCanonicalWithMapping(ctx, chunkCh, mapping)
		} else {
			eventCh, translatorErrCh = translator.StreamEventsFromCanonical(ctx, chunkCh)
		}
	} else {
		eventCh, translatorErrCh = translator.StreamEventsFromCanonical(ctx, chunkCh)
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported", "request_id", rc.RequestID)
		return
	}
	flusher.Flush()

	// Drain events and errors concurrently.
	// translatorErrCh closes last; eventCh closes before or with it.
	for {
		select {
		case <-ctx.Done():
			return

		case event, ok := <-eventCh:
			if !ok {
				// Event channel closed — translator is done.
				eventCh = nil
				continue
			}
			if _, writeErr := w.Write(event); writeErr != nil {
				slog.Warn("error writing translated anthropic stream", "request_id", rc.RequestID, "error", writeErr)
				return
			}
			flusher.Flush()

		case err, ok := <-translatorErrCh:
			if !ok {
				// Error channel closed. Don't return yet — eventCh may still
				// hold the final message_delta / message_stop events (the
				// translator's defer ordering closes events before errs but
				// without this guard the handler would exit before draining
				// them).
				translatorErrCh = nil
				continue
			}
			if err != nil {
				slog.Warn("translator stream error", "request_id", rc.RequestID, "error", err)
				// Error event already emitted by the translator; just stop.
				return
			}

		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				continue
			}
			if err != nil {
				slog.Warn("provider stream error", "request_id", rc.RequestID, "error", err)
				return
			}
		}

		// Once every channel is drained, we're done.
		if eventCh == nil && errCh == nil && translatorErrCh == nil {
			return
		}
	}
}

// writeAnthropicErrorFromError converts an error to Anthropic error format.
func writeAnthropicErrorFromError(w http.ResponseWriter, err error) {
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		anthropicfmt.WriteAPIError(w, apiErr)
		return
	}
	anthropicfmt.WriteError(w, http.StatusInternalServerError, "api_error", err.Error())
}

// extractToolNameMapping returns the truncated→original tool name map stashed
// by the Anthropic translator's RequestToCanonical when it had to shorten a
// tool name to fit OpenAI's 64-char limit. Returns nil if no mapping is set
// or the encoded value is malformed (non-fatal — restoration is skipped).
func extractToolNameMapping(req *models.ChatCompletionRequest) map[string]string {
	if req == nil || req.Extra == nil {
		return nil
	}
	raw, ok := req.Extra["tool_name_mapping"]
	if !ok {
		return nil
	}
	var m map[string]string
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil
	}
	return m
}
