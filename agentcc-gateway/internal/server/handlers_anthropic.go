package server

import (
	"bytes"
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
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	anthropicprov "github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/translation"
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
		slog.Warn("failed to read anthropic request body", "request_id", rc.RequestID,
			"content_length", r.ContentLength, "error", err)
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
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON: "+models.DescribeJSONError(parseErr))
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
	rc.Metadata["client_ip"] = middleware.ClientIP(r)

	// Extract headers.
	setAuthMetadataFromRequest(rc, r)
	readGuardrailPolicyHeader(rc, r)
	retryOverrides, apiErr := routing.ParseRetryOverrides(r.Header)
	if apiErr != nil {
		writeAnthropicErrorFromError(w, apiErr)
		return
	}
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(routing.WithRetryOverrides(r.Context(), retryOverrides), timeout)
	defer cancel()

	provider, orgCfg, unreadable, err := h.resolveAnthropicRoute(ctx, r, rc, body)
	if err != nil {
		writeAnthropicErrorFromError(w, err)
		return
	}
	upstreamModel := rc.Request.Model

	// Build Anthropic-specific headers to forward.
	anthropicHeaders := make(map[string]string)
	for _, key := range []string{"anthropic-version", "anthropic-beta", "anthropic-dangerous-direct-browser-access"} {
		if v := r.Header.Get(key); v != "" {
			anthropicHeaders[key] = v
		}
	}

	// Credit-funded requests always take canonical execution, including its
	// allowlist and cost reporting. A BYOK primary can also fall back to
	// credits, so refuse unsupported routing controls on EVERY canonical
	// path before translation drops them and before any attempt or hold.
	// Only lossless native pass-through may forward those fields unchanged.
	managed := credentialType(rc) == models.CredentialManaged
	ap, native := provider.(providers.AnthropicNativeProvider)
	nativePassThrough := native && !managed && !h.messagesRoutingChain(rc, orgCfg)
	if !nativePassThrough {
		if apiErr := h.managedMessagesRouting(rc, body); apiErr != nil {
			writeAnthropicErrorFromError(w, apiErr)
			return
		}
	}

	// Fast path: provider natively speaks Anthropic. The body reaches it as
	// written, so it names the id the org registered when the org's own
	// provider took the request under it (passThroughModel).
	if nativePassThrough {
		if sent := passThroughModel(rc, upstreamModel); rc.Metadata["model_alias"] != "" || sent != model {
			body, err = rewriteRequestModel(body, sent)
			if err != nil {
				anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON: "+models.DescribeJSONError(err))
				return
			}
		}
		// rc.Request is left nil past the eligibility judgement below, as it
		// is on the genai path. A carrier with empty Messages would hash to
		// the same cache key for every prompt (BuildCacheKey marshals
		// Messages), and the response stored against it holds usage only —
		// so a later request would be served a content-less 200.
		// Request-shaped plugins skip on nil. The guardrail checks on the
		// request read the body the provider gets, in the chat shape, from
		// rc.PassThrough, and a check's rewrite of it (a remediation) is
		// written into the body sent.
		passThrough := requestview.Anthropic(body)
		rc.PassThrough = passThrough
		rc.Request = nil

		if isStream {
			h.handleAnthropicStream(ctx, w, rc, ap, passThrough, anthropicHeaders)
		} else {
			h.handleAnthropicNonStream(ctx, w, rc, ap, passThrough, anthropicHeaders)
		}
		return
	}

	// Unlike native pass-through, a routed canonical request must be fully
	// readable; otherwise routing/translation could silently lose its input.
	if unreadable != "" {
		writeAnthropicErrorFromError(w, models.ErrBadRequest("invalid_request", unreadable))
		return
	}

	// Every canonical route extracts only text from nested tool results. Refuse
	// incompatible content before translation, pipeline credit holds or attempts,
	// regardless of the primary provider, funding source or fallback config.
	if err := messagesToolResultTranslationError(body); err != nil {
		writeAnthropicErrorFromError(w, err)
		return
	}

	// Translation path: provider speaks OpenAI-canonical internally.
	translator, ok := translation.InboundFor("anthropic")
	if !ok {
		// Should never happen — init() registers it.
		anthropicfmt.WriteError(w, http.StatusInternalServerError, "api_error", "anthropic translator not registered")
		return
	}

	if _, native := provider.(providers.AnthropicNativeProvider); native && !managed {
		// Canonical adapters do not forward native version/beta headers.
		if anthropicHeaders["anthropic-beta"] != "" || (anthropicHeaders["anthropic-version"] != "" && anthropicHeaders["anthropic-version"] != "2023-06-01") {
			writeAnthropicErrorFromError(w, models.ErrBadRequest("messages_routing_translation_unsupported",
				"Native version or beta headers cannot be preserved by the configured Messages routing chain"))
			return
		}
		if err := messagesRoutedTranslationError(body); err != nil {
			writeAnthropicErrorFromError(w, err)
			return
		}
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
	canonicalReq.Stream = isStream

	// Every routed request is judged by the eligibility engine as chat's
	// route is (resolveRoute), on the translated request, whoever's key it
	// goes out on: the key's ACL, the org's provider lock and data policy,
	// the model's catalogue state and the request's tools, and for a Future
	// AGI request the org's managed allow and deny lists and the
	// billing-mode restriction. Its verdict gives the attempt the endpoint
	// set and the effective data policy the upstream is pinned to.
	rc.Request = canonicalReq
	// The route is chosen: fix the identity the request's fallback plan is
	// judged for and dispatched on (fallback_plan.go).
	fixFallbackRoute(rc)
	if v := h.checkEligibility(rc, orgCfg, rc.Provider, upstreamModel); v != nil {
		writeAnthropicErrorFromError(w, v.apiError())
		return
	}

	if isStream {
		h.handleAnthropicStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq, orgCfg)
	} else {
		h.handleAnthropicNonStreamViaCanonical(ctx, w, rc, provider, translator, canonicalReq, orgCfg)
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
		slog.Warn("failed to read anthropic token-count request body", "request_id", rc.RequestID,
			"content_length", r.ContentLength, "error", err)
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Failed to read request body")
		return
	}
	if int64(len(body)) > h.maxBodySize {
		anthropicfmt.WriteError(w, http.StatusRequestEntityTooLarge, "invalid_request_error",
			fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize))
		return
	}

	var req struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON in request body: "+models.DescribeJSONError(err))
		return
	}
	model := req.Model
	if model == "" {
		anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "model is required")
		return
	}

	rc.Model = model
	rc.Metadata["client_ip"] = middleware.ClientIP(r)
	setAuthMetadataFromRequest(rc, r)
	readGuardrailPolicyHeader(rc, r)

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	provider, orgCfg, _, err := h.resolveAnthropicRoute(ctx, r, rc, body)
	if err != nil {
		writeAnthropicErrorFromError(w, err)
		return
	}
	upstreamModel := rc.Request.Model

	// Counting sends the prompt upstream too. Apply chat's eligibility rules,
	// but render every refusal in this endpoint's Anthropic envelope, just as
	// the unknown-model path above does.
	fixFallbackRoute(rc)
	if v := h.checkEligibility(rc, orgCfg, rc.Provider, upstreamModel); v != nil {
		writeAnthropicErrorFromError(w, v.apiError())
		return
	}

	// Selection is the Messages route, not a separate BYOK alternative. Counting
	// cannot create a managed hold or generation. Refuse a credit-funded route
	// before the pipeline instead of returning a count for a different model.
	if credentialType(rc) == models.CredentialManaged {
		h.setAgentccHeaders(w, rc)
		anthropicfmt.WriteError(w, http.StatusNotImplemented, "not_supported_error",
			"count_tokens is not supported for a Future AGI credits route; use /v1/messages for usage")
		return
	}
	// The canonical view was for route judgement only. Native checks read the
	// count-specific PassThrough view; neither a generation nor usage is stored.
	rc.Request = nil

	// Fast path: native Anthropic provider supports count_tokens. The body
	// names the model as /v1/messages's does (passThroughModel).
	if ap, ok := provider.(providers.AnthropicNativeProvider); ok {
		if sent := passThroughModel(rc, upstreamModel); rc.Metadata["model_alias"] != "" || sent != model {
			body, err = rewriteRequestModel(body, sent)
			if err != nil {
				anthropicfmt.WriteError(w, http.StatusBadRequest, "invalid_request_error", "Invalid JSON: "+models.DescribeJSONError(err))
				return
			}
		}
		anthropicHeaders := make(map[string]string)
		for _, key := range []string{"anthropic-version", "anthropic-beta", "anthropic-dangerous-direct-browser-access"} {
			if v := r.Header.Get(key); v != "" {
				anthropicHeaders[key] = v
			}
		}
		// The provider gets the whole prompt to count it, so the request
		// goes through the pipeline as /v1/messages does: the key is
		// authenticated, the call is rate-limited and logged, and the checks
		// on the request read the body (rc.PassThrough; rc.Request stays
		// nil). A check that blocks refuses it before the provider is
		// called, and a check's rewrite reaches the provider.
		passThrough := requestview.AnthropicCountTokens(body)
		rc.PassThrough = passThrough
		var respBody []byte
		var statusCode int
		if err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
			var callErr error
			respBody, statusCode, callErr = ap.CountAnthropicTokens(ctx, passThrough.Body(), anthropicHeaders)
			if callErr == nil {
				// The answer is passed through as the provider sent it: the
				// log records its status and the count (rc has no answer).
				models.RecordPassedThroughAnswer(rc, statusCode, respBody, true)
			}
			return callErr
		}); err != nil {
			writeAnthropicErrorFromError(w, providerFailure(rc, err))
			return
		}
		if failure := nativeProviderFailure(rc, statusCode, respBody); failure != nil {
			h.setAgentccHeaders(w, rc)
			writeAnthropicErrorFromError(w, failure)
			return
		}
		if respBody == nil {
			writeAnthropicErrorFromError(w, models.ErrInternal("no response from provider"))
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

// rewriteRequestModel changes only the routed model while preserving every
// other field of a JSON request body forwarded upstream as-is.
func rewriteRequestModel(body []byte, model string) ([]byte, error) {
	var request map[string]json.RawMessage
	if err := json.Unmarshal(body, &request); err != nil {
		return nil, err
	}
	encodedModel, err := json.Marshal(model)
	if err != nil {
		return nil, err
	}
	request["model"] = encodedModel
	return json.Marshal(request)
}

// ─── Native pass-through helpers (unchanged from original) ───────────────────

func (h *Handlers) handleAnthropicNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, ap providers.AnthropicNativeProvider, req *requestview.View, headers map[string]string) {
	var respBody []byte
	var statusCode int

	err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		// The body after the checks on the request, with any rewrite of
		// theirs.
		respBody, statusCode, callErr = ap.CreateAnthropicMessage(ctx, req.Body(), headers)
		if callErr != nil {
			return callErr
		}
		if statusCode < 400 {
			applyAnthropicUsage(rc, respBody)
			// An output check reads the answer the client gets, not only its
			// usage.
			if rc.Flags.AnswerReaders != 0 {
				if answer, err := anthropicprov.AnswerFromNative(respBody); err == nil {
					giveChecksTheAnswer(rc, answer)
				} else {
					markAnswerUnreadable(rc)
				}
			}
		}
		return nil
	})
	// An output check that withheld the answer is refused like a pre-stage
	// block, and respBody is never served.
	if err = pipelineErr(rc, err); err != nil {
		writeAnthropicErrorFromError(w, providerFailure(rc, err))
		return
	}

	if failure := nativeProviderFailure(rc, statusCode, respBody); failure != nil {
		h.setAgentccHeaders(w, rc)
		writeAnthropicErrorFromError(w, failure)
		return
	}

	// A plugin answered it — a cache hit. Its response is canonical.
	if respBody == nil {
		h.writeAnthropicShortCircuit(w, rc)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(respBody)
}

// nativeErrorMessage is the message of a provider's native error answer,
// {"error":{"message":...}} as Anthropic and OpenAI write it, else the body
// itself, cut to 500 bytes.
func nativeErrorMessage(body []byte) string {
	var answer struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &answer) == nil && answer.Error.Message != "" {
		return answer.Error.Message
	}
	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return msg
}

// applyAnthropicUsage lifts the usage block onto rc. rc.Response is populated
// because that is what the cost plugin prices from; it carries usage only, not
// a translation of the response.
func applyAnthropicUsage(rc *models.RequestContext, respBody []byte) {
	metered := anthropicfmt.ExtractUsageEvidence(respBody)
	inputTokens, outputTokens := 0, 0
	if metered != nil {
		inputTokens, outputTokens = metered.PromptTokens, metered.CompletionTokens
	}
	if inputTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(inputTokens)
	}
	if outputTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(outputTokens)
	}
	if resolvedModel := anthropicfmt.ExtractModel(respBody); resolvedModel != "" {
		rc.ResolvedModel = resolvedModel
	}
	// Server-tool counters (web search, web fetch) are metered on the native
	// path too (W2c integration, V2 IR-2).
	serverTools := anthropicprov.ServerToolUsage(respBody)
	if metered == nil && serverTools == nil {
		return
	}
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	if metered == nil {
		metered = &models.Usage{}
	}
	metered.ServerToolUse = serverTools
	rc.Response = &models.ChatCompletionResponse{Model: model, Usage: metered}
}

// writeAnthropicShortCircuit renders a plugin-produced response in Anthropic
// wire format.
func (h *Handlers) writeAnthropicShortCircuit(w http.ResponseWriter, rc *models.RequestContext) {
	translator, ok := translation.InboundFor("anthropic")
	if !ok || rc.Response == nil {
		h.setAgentccHeaders(w, rc)
		anthropicfmt.WriteError(w, http.StatusInternalServerError, "api_error",
			"request was answered internally but the response could not be formatted")
		return
	}
	out, err := translator.ResponseFromCanonical(rc.Response)
	if err != nil {
		h.setAgentccHeaders(w, rc)
		anthropicfmt.WriteError(w, http.StatusInternalServerError, "api_error",
			"failed to format response: "+err.Error())
		return
	}
	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(out)
}

func (h *Handlers) handleAnthropicStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, ap providers.AnthropicNativeProvider, req *requestview.View, headers map[string]string) {
	var stream io.ReadCloser
	var statusCode int

	// Post-plugins wait for the close, when the counts are known.
	err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var callErr error
		// The body after the checks on the request, with any rewrite of
		// theirs.
		stream, statusCode, callErr = ap.StreamAnthropicMessage(ctx, req.Body(), headers)
		return callErr
	})
	// A cache hit an output check withheld is refused like a pre-stage block.
	if err = pipelineErr(rc, err); err != nil {
		writeAnthropicErrorFromError(w, nativeStreamProviderFailure(rc, err))
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		// JSON even though a stream was asked for, as the chat handler does.
		h.writeAnthropicShortCircuit(w, rc)
		return
	}
	if stream == nil {
		writeAnthropicErrorFromError(w, models.ErrInternal("no stream from provider"))
		return
	}
	defer stream.Close()

	// Without this the stream would be priced at zero and logged as such.
	var usage anthropicfmt.StreamUsageScanner
	// The text the stream sends, for an output check that reads the answer.
	answer := newSSEAnswer(rc.Flags.AnswerReaders != 0, anthropicStreamDelta)
	// A stream that a blocking check on the answer reads is held until the
	// checks have passed the whole answer (heldStream).
	var held *heldStream
	if holdsTheStream(rc) {
		held = newHeldStream(w)
		answer.holdAll()
	}
	finalize := func(detach bool) {
		applyAnthropicStreamUsage(rc, &usage)
		answer.giveTo(rc)
		held.beforeChecks(rc, answer.complete())
		h.engine.RunPostPlugins(checksContext(ctx, held, detach), rc)
	}

	// If upstream returned an error status, read and forward. Still finalised:
	// the non-streaming path records a 4xx through Process, and a streamed one
	// that skipped it would be missing from the log and the trace.
	if statusCode >= 400 {
		errBody, _ := io.ReadAll(io.LimitReader(stream, 1024*1024))
		h.setAgentccHeaders(w, rc)
		writeAnthropicErrorFromError(w, nativeProviderFailure(rc, statusCode, errBody))
		finalize(false)
		return
	}

	if held != nil {
		w = held
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

	// Relay SSE stream from Anthropic to client.
	buf := make([]byte, 32*1024)
	for {
		n, readErr := stream.Read(buf)
		if n > 0 {
			usage.Write(buf[:n])
			answer.Write(buf[:n])
			if _, writeErr := w.Write(buf[:n]); writeErr != nil {
				slog.Warn("error writing anthropic stream", "request_id", rc.RequestID, "error", writeErr)
				// Client gone, tokens still spent — detach from its context.
				finalize(true)
				return
			}
			flusher.Flush()
		}
		if readErr != nil {
			if readErr != io.EOF {
				slog.Warn("error reading anthropic stream", "request_id", rc.RequestID, "error", readErr)
			}
			finalize(false)
			if readErr == io.EOF {
				endStream(w, flusher, held, rc, anthropicfmt.StreamErrorEvent)
			} else {
				settleHeldStream(held, rc, anthropicfmt.StreamErrorEvent)
			}
			return
		}

		select {
		case <-ctx.Done():
			finalize(true)
			settleHeldStream(held, rc, anthropicfmt.StreamErrorEvent)
			return
		default:
		}
	}
}

// applyAnthropicStreamUsage moves collected counts onto rc.
func applyAnthropicStreamUsage(rc *models.RequestContext, usage *anthropicfmt.StreamUsageScanner) {
	if model := usage.Model(); model != "" {
		rc.ResolvedModel = model
	}
	metered := usage.UsageEvidence()
	if metered == nil {
		// Leave rc.Response nil so the cost plugin skips it — a gap is honest
		// where a zero is not.
		return
	}
	inputTokens, outputTokens := metered.PromptTokens, metered.CompletionTokens
	if inputTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(inputTokens)
	}
	if outputTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(outputTokens)
	}
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	rc.Response = &models.ChatCompletionResponse{Model: model, Usage: metered}
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
	orgConfigs ...*tenant.OrgConfig,
) {
	var orgCfg *tenant.OrgConfig
	if len(orgConfigs) > 0 {
		orgCfg = orgConfigs[0]
	}
	// Without this the pipeline sees a nil rc.Request.
	rc.Request = canonicalReq
	mapping := takeToolNameMapping(canonicalReq)
	// After the mapping is out of Extra, before the pre-plugins hold credit.
	if apiErr := h.checkRoutedRequest(rc); apiErr != nil {
		writeAnthropicErrorFromError(w, apiErr)
		return
	}

	_, err := h.runNonStream(ctx, rc, provider, orgCfg)
	canonicalResp := rc.Response

	// An output check that withheld the answer is refused like a pre-stage
	// block, and canonicalResp is never served.
	if err = pipelineErr(rc, err); err != nil {
		err = providerFailure(rc, err)
		var apiErr *models.APIError
		if errors.As(err, &apiErr) {
			if meta, ok := apiErr.Metadata.(map[string]any); ok {
				if _, attributed := meta["provider"]; attributed {
					h.setAgentccHeaders(w, rc)
					writeAnthropicErrorFromError(w, apiErr)
					return
				}
			}
			status, body, ct := translator.ErrorFromCanonical(apiErr)
			h.setAgentccHeaders(w, rc)
			models.SetRetryAfter(w, apiErr)
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
	if alias := rc.Metadata["model_alias"]; alias != "" {
		wireResp := *canonicalResp
		wireResp.Model = alias
		canonicalResp = &wireResp
	}

	// Convert response back to Anthropic wire format.
	// If the original request had a tool_name_mapping, restore truncated names.
	var respBody []byte
	var marshalErr error

	if mapping != nil {
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
	orgConfigs ...*tenant.OrgConfig,
) {
	var orgCfg *tenant.OrgConfig
	if len(orgConfigs) > 0 {
		orgCfg = orgConfigs[0]
	}
	rc.Request = canonicalReq
	mapping := takeToolNameMapping(canonicalReq)
	// After the mapping is out of Extra, before the pre-plugins hold credit.
	if apiErr := h.checkRoutedRequest(rc); apiErr != nil {
		writeAnthropicErrorFromError(w, apiErr)
		return
	}
	// Ask every canonical stream for usage, as chat does. A BYOK primary can
	// fall back to a credit-funded model, so this cannot depend on the initial
	// credential type. Anthropic adapters map usage into their native dialect.
	if canonicalReq.StreamOptions == nil {
		canonicalReq.StreamOptions = &models.StreamOptions{IncludeUsage: true}
	} else {
		canonicalReq.StreamOptions.IncludeUsage = true
	}
	streamCtx, streamCancel := context.WithCancel(ctx)
	defer streamCancel()

	opened, err := h.openRoutedChatStream(streamCtx, rc, provider, orgCfg)
	if err != nil {
		writeAnthropicErrorFromError(w, providerFailure(rc, h.attemptLimitError(w, rc, err)))
		return
	}
	defer func() {
		if opened != nil && opened.cancel != nil {
			opened.cancel()
		}
	}()

	// A cache hit an output check withheld is refused like a pre-stage block.
	if err = pipelineErr(rc, err); err != nil {
		writeAnthropicErrorFromError(w, err)
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		// Answered by a plugin, returned as JSON — the canonical chat handler
		// does the same for a cache hit on a streaming request.
		h.writeAnthropicShortCircuit(w, rc)
		return
	}
	if opened == nil {
		writeAnthropicErrorFromError(w, models.ErrInternal("no stream from provider"))
		return
	}
	rawChunkCh, errCh, firstChunk, streamPrefixes := opened.chunks, opened.errors, opened.first, opened.prefixes
	if rawChunkCh == nil || firstChunk == nil {
		writeAnthropicErrorFromError(w, models.ErrInternal("no stream from provider"))
		return
	}
	// Reinsert the already-observed first chunk for the usage tee and
	// Anthropic translator. Stop forwarding promptly if the client leaves.
	startedChunks := make(chan models.StreamChunk)
	go func() {
		defer close(startedChunks)
		for _, chunk := range append(streamPrefixes, *firstChunk) {
			// The start gate has already observed the concrete completion.
			// Sparse replayed metadata must not open message_start with an
			// empty model or a generated ID. Enrich only this dialect's copy;
			// chat keeps the original prefix bytes and order.
			if chunk.Model == "" {
				chunk.Model = firstChunk.Model
			}
			if chunk.ID == "" {
				chunk.ID = firstChunk.ID
			}
			select {
			case startedChunks <- chunk:
			case <-streamCtx.Done():
				return
			}
		}
		for {
			select {
			case chunk, ok := <-rawChunkCh:
				if !ok {
					return
				}
				select {
				case startedChunks <- chunk:
				case <-streamCtx.Done():
					return
				}
			case <-streamCtx.Done():
				return
			}
		}
	}()

	// Tee for usage: the counts are already structured here, unlike in the
	// translated SSE bytes. It assembles the text for an output check that
	// reads the answer.
	capture := newStreamCapture(rc.Flags.AnswerReaders != 0)
	// A stream that a blocking check on the answer reads is held until the
	// checks have passed the whole answer (heldStream).
	var held *heldStream
	if holdsTheStream(rc) {
		held = newHeldStream(w)
		capture.holdAll()
	}
	chunkCh, usageCh := teeStreamUsage(streamCtx, startedChunks, capture)
	chunkCh = anthropicStreamEstimates(streamCtx, chunkCh, estimatedManagedStreamUsage(rc, &streamedOutput{}))
	if alias := rc.Metadata["model_alias"]; alias != "" {
		chunkCh = rewriteStreamModel(streamCtx, chunkCh, alias)
	}

	// Freeze response-bound ownership before finalizing the translated stream.
	streamAttemptID := latestStreamAttempt(rc)
	var terminalEvidence *models.ProviderBillingEvidence
	// Terminal paths may stop consuming events while the translator is blocked
	// on its event buffer. Drain the usage tee independently, without canceling
	// the replay or upstream: both may still hold output and billing evidence.
	// Only this handler goroutine starts the drain, including translator errors.
	drainingUsage := false
	drainUsage := func() {
		if !drainingUsage {
			drainingUsage = true
			go func() {
				for range chunkCh {
				}
			}()
		}
	}
	// Every terminal exit below must drain -> finalize -> cancel. Canceling
	// first can discard even the already-admitted first chunk during replay.
	finalize := func(detach bool) {
		drainUsage()
		applyCanonicalStreamUsageForAttempt(rc, usageCh, streamAttemptID)
		applyStreamFailureEvidence(rc, streamAttemptID, terminalEvidence)
		// After the usage: the tee has finished writing the capture.
		giveChecksTheStreamedAnswer(rc, capture)
		held.beforeChecks(rc, capture.complete())
		h.engine.RunPostPlugins(checksContext(ctx, held, detach), rc)
		streamCancel()
		opened.cancel()
	}

	// Translate the OpenAI chunk stream into Anthropic SSE events. If the
	// request came in with a tool_name_mapping (a tool was truncated to fit
	// OpenAI's 64-char limit during RequestToCanonical), use the variant that
	// restores original names on content_block_start tool_use events —
	// otherwise the client would see the truncated name in the stream.
	var eventCh <-chan []byte
	var translatorErrCh <-chan error
	if mapping != nil {
		type streamMappingRestorer interface {
			StreamEventsFromCanonicalWithMapping(ctx context.Context, chunks <-chan models.StreamChunk, mapping map[string]string) (<-chan []byte, <-chan error)
		}
		if smr, ok := translator.(streamMappingRestorer); ok {
			eventCh, translatorErrCh = smr.StreamEventsFromCanonicalWithMapping(streamCtx, chunkCh, mapping)
		} else {
			eventCh, translatorErrCh = translator.StreamEventsFromCanonical(streamCtx, chunkCh)
		}
	} else {
		eventCh, translatorErrCh = translator.StreamEventsFromCanonical(streamCtx, chunkCh)
	}

	if held != nil {
		w = held
	}
	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported", "request_id", rc.RequestID)
		finalize(true)
		return
	}
	flusher.Flush()

	// A provider failure is terminal, but it cannot overtake output already
	// admitted by the start gate. Keep the translator as the sole chunk reader
	// until EOF: it delivers the output and keeps the usage tee draining even
	// when the provider queues more than an event buffer before its final bill.
	// After that delivery, finalize drains and settles before canceling; no
	// terminal failure may open another routing attempt.
	var providerErr *models.APIError
	recordProviderError := func(err error) {
		if err == nil || providerErr != nil {
			return
		}
		slog.Warn("provider stream error", "request_id", rc.RequestID, "error", err)
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) {
			apiErr = models.ErrUpstreamProvider(http.StatusBadGateway, err.Error())
		}
		terminalEvidence = apiErr.BillingEvidence
		wireErr := *apiErr
		wireErr.Message = models.RedactCredentials(wireErr.Message)
		providerErr = &wireErr
	}
	drainReadyProviderErrors := func() {
		// Adapters publish terminal errors before closing chunks. The
		// translator's terminal events follow that EOF, so any queued error
		// must win over a successful stop, even if the event wins the select.
		// Do not wait for an auxiliary error channel that may stay open.
		for errCh != nil {
			select {
			case err, ok := <-errCh:
				if !ok {
					errCh = nil
				} else {
					recordProviderError(err)
				}
			default:
				return
			}
		}
	}
	endProviderFailure := func() {
		if settleHeldStream(held, rc, anthropicfmt.StreamErrorEvent) {
			return
		}
		apiErr := providerErr
		if withheld := withheldAnswerError(rc); withheld != nil {
			apiErr = withheld
		}
		if _, writeErr := w.Write(anthropicfmt.StreamErrorEvent(apiErr)); writeErr != nil {
			slog.Warn("error writing anthropic provider error", "request_id", rc.RequestID, "error", writeErr)
			return
		}
		flusher.Flush()
	}

	// Drain events and errors concurrently.
	// translatorErrCh closes last; eventCh closes before or with it.
	translatorFailed := false
	for {
		select {
		case <-ctx.Done():
			finalize(true)
			settleHeldStream(held, rc, anthropicfmt.StreamErrorEvent)
			return

		case event, ok := <-eventCh:
			if !ok {
				// Event channel closed — translator is done.
				eventCh = nil
				break
			}
			isStop := bytes.HasPrefix(event, []byte("event: message_stop\n"))
			if isStop || bytes.HasPrefix(event, []byte("event: message_delta\n")) {
				drainReadyProviderErrors()
				if providerErr != nil {
					// Suppress the synthesized success markers, not the preceding
					// output. At message_stop, translation and accounting reached
					// chunk EOF; replace it with a single native error event.
					if isStop {
						finalize(false)
						endProviderFailure()
						return
					}
					continue
				}
			}
			if _, writeErr := w.Write(event); writeErr != nil {
				slog.Warn("error writing translated anthropic stream", "request_id", rc.RequestID, "error", writeErr)
				finalize(true)
				return
			}
			flusher.Flush()
			if isStop {
				// The canonical translator emits message_stop only after chunks
				// close, so the usage tee has all trailing evidence. Do not wait
				// for auxiliary error channels that a provider may leave open.
				finalize(false)
				endStream(w, flusher, held, rc, anthropicfmt.StreamErrorEvent)
				return
			}

		case err, ok := <-translatorErrCh:
			if !ok {
				// Error channel closed. Don't return yet — eventCh may still
				// hold the final message_delta / message_stop events (the
				// translator's defer ordering closes events before errs but
				// without this guard the handler would exit before draining
				// them).
				translatorErrCh = nil
				break
			}
			if err != nil {
				slog.Warn("translator stream error", "request_id", rc.RequestID, "error", err)
				if !translatorFailed {
					translatorFailed = true
					// The translator no longer consumes chunks. Keep the usage
					// tee moving through trailing billing evidence, without
					// canceling the upstream. Drain its error event too: it may
					// still be queued when the error channel wins this select.
					drainUsage()
				}
			}

		case err, ok := <-errCh:
			if !ok {
				errCh = nil
				break
			}
			recordProviderError(err)
		}

		if translatorFailed && eventCh == nil && translatorErrCh == nil {
			finalize(false)
			settleHeldStream(held, rc, anthropicfmt.StreamErrorEvent)
			return
		}
		// Once every channel is drained, we're done.
		if eventCh == nil && errCh == nil && translatorErrCh == nil {
			finalize(false)
			if providerErr != nil {
				endProviderFailure()
			} else {
				endStream(w, flusher, held, rc, anthropicfmt.StreamErrorEvent)
			}
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

// takeToolNameMapping removes and returns the truncated→original tool name map
// stashed by the Anthropic translator's RequestToCanonical when it had to
// shorten a tool name to fit OpenAI's 64-char limit. It must run before the
// provider call: Extra is forwarded upstream verbatim. Returns nil if no
// mapping is set or the encoded value is malformed (restoration is skipped).
func takeToolNameMapping(req *models.ChatCompletionRequest) map[string]string {
	raw, ok := req.Extra["tool_name_mapping"]
	if !ok {
		return nil
	}
	delete(req.Extra, "tool_name_mapping")
	var m map[string]string
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil
	}
	return m
}

// streamUsage is what a finished stream reported about itself, and the
// output it carried, which bills a Future AGI stream that ended before its
// upstream reported usage (applyCanonicalStreamUsage).
type streamUsage struct {
	usage    *models.Usage
	model    string
	streamed streamedOutput
}

// teeStreamUsage passes chunks through untouched, keeping the last usage and
// model, counting the output of every chunk, and assembling the text into
// capture when it is not nil. The result travels on a channel so reading it,
// or capture, after the drain loop is ordered against the goroutine that
// wrote it. first holds any chunks consumed by the admission handshake; they
// are metered and forwarded before in, through the same path as later chunks.
func teeStreamUsage(ctx context.Context, in <-chan models.StreamChunk, capture *streamCapture, first ...models.StreamChunk) (<-chan models.StreamChunk, <-chan streamUsage) {
	out := make(chan models.StreamChunk)
	done := make(chan streamUsage, 1)

	record := func(seen *streamUsage, chunk models.StreamChunk) {
		if chunk.Usage != nil {
			seen.usage = models.MergeUsage(seen.usage, chunk.Usage)
		}
		if chunk.Model != "" {
			seen.model = chunk.Model
		}
		seen.streamed.observe(chunk)
		capture.observe(chunk)
	}

	go func() {
		var seen streamUsage
		defer func() {
			done <- seen
			close(done)
			close(out)
		}()
		forwarding := true
		forward := func(chunk models.StreamChunk) {
			record(&seen, chunk)
			if !forwarding {
				return
			}
			select {
			case out <- chunk:
			case <-ctx.Done():
				// The consumer is gone. Keep draining and recording usage,
				// but never block forwarding to the abandoned translator.
				forwarding = false
			}
		}
		for _, chunk := range first {
			forward(chunk)
		}
		for chunk := range in {
			forward(chunk)
		}
	}()

	return out, done
}

// rewriteStreamModel keeps the Anthropic wire response consistent with the Claude-shaped
// alias accepted by Claude Code. Usage collection happens before this rewrite, so telemetry
// still records the concrete upstream model.
func rewriteStreamModel(ctx context.Context, in <-chan models.StreamChunk, model string) <-chan models.StreamChunk {
	out := make(chan models.StreamChunk)
	go func() {
		defer close(out)
		for chunk := range in {
			chunk.Model = model
			select {
			case out <- chunk:
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

// applyCanonicalStreamUsage moves a finished stream's usage onto rc: the
// usage the upstream reported, which always wins, or, for a Future AGI
// (managed) stream that ended before the upstream reported any (the client
// left, the upstream failed, a guardrail withheld the answer, the upstream
// sends none), an estimate from the request and the output the stream
// carried, as chat bills the same stream (estimatedManagedStreamUsage).
// Any other stream without usage leaves rc.Response nil, so the cost plugin
// skips rather than bills zero.
func applyCanonicalStreamUsage(rc *models.RequestContext, usageCh <-chan streamUsage) {
	applyCanonicalStreamUsageForAttempt(rc, usageCh, "")
}

func applyCanonicalStreamUsageForAttempt(rc *models.RequestContext, usageCh <-chan streamUsage, attemptID string) {
	seen := <-usageCh
	bindStreamEvidence(rc, attemptID, seen.streamed.generation, seen.usage)
	if seen.model != "" {
		rc.ResolvedModel = seen.model
	}
	metered := seen.usage
	if metered.NeedsTokenEstimate() {
		metered = billing.CompleteUsage(metered, estimatedManagedStreamUsage(rc, &seen.streamed))
	}
	if metered == nil {
		return
	}
	if metered.Estimated {
		rc.Metadata["usage_estimated"] = "true"
	}
	if metered.PromptTokens > 0 {
		rc.Metadata["input_tokens"] = strconv.Itoa(metered.PromptTokens)
	}
	if metered.CompletionTokens > 0 {
		rc.Metadata["output_tokens"] = strconv.Itoa(metered.CompletionTokens)
	}
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	usage := *metered
	if usage.TotalTokens == 0 {
		// Providers vary on whether they send it; the native paths always fill
		// it, so do the same here rather than diverge.
		usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
	}
	rc.Response = &models.ChatCompletionResponse{ID: seen.streamed.generation.ID, Model: model, Usage: &usage, UpstreamProvider: seen.streamed.provider}
}

// setUsageResponse gives the post-plugins the minimal canonical response they
// price from. The dialect handlers forward native bytes and never build one.
func setUsageResponse(rc *models.RequestContext, usage *models.Usage) {
	model := rc.ResolvedModel
	if model == "" {
		model = rc.Model
	}
	rc.Response = &models.ChatCompletionResponse{Model: model, Usage: usage}
}
