package server

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// ---------------------------------------------------------------------------
// Helper: resolve provider for Assistants and Vector Stores endpoints
// ---------------------------------------------------------------------------

// openAIProviderID names the provider Assistants and Vector Stores requests go
// to. Both are OpenAI APIs proxied path for path, so they need OpenAI itself;
// any OpenAI-compatible provider has the same Go type but not these APIs, and
// assistants, threads and stores live wherever they were created.
const openAIProviderID = "openai"

// resolveOpenAIProvider returns the org's own "openai" provider when it has
// one configured, and otherwise the global one, which keys limited to their
// org's providers may not use. A global "openai" provider that is managed
// (config.yaml managed: true, FutureAGI's credentials) never serves these
// APIs, to any key: the gateway prices none of their usage, so managed
// credentials serve only what it meters (501 not_supported).
func (h *Handlers) resolveOpenAIProvider(ctx context.Context, rc *models.RequestContext) (providers.Provider, error) {
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" && orgCfg != nil && h.orgProviderCache != nil {
		if pcfg := orgCfg.Providers[openAIProviderID]; pcfg != nil && pcfg.Enabled && pcfg.HasCredentials() {
			p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, openAIProviderID, pcfg.APIKey, pcfg)
			if err != nil {
				return nil, &models.APIError{
					Status:  http.StatusBadGateway,
					Type:    models.ErrTypeServer,
					Code:    "provider_unavailable",
					Message: "your organization's OpenAI provider could not be set up; check its configuration",
				}
			}
			rc.Provider = openAIProviderID
			rc.Metadata["billing_mode"] = billingBYOK // the org's own key
			return p, nil
		}
	}

	p, ok := h.registry.GetProvider(openAIProviderID)
	if ok && h.registry.IsManaged(openAIProviderID) {
		return nil, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("this API is served only with your organization's own OpenAI key or the operator's; the managed provider %q does not serve it", openAIProviderID),
		}
	}
	if !ok || h.orgProvidersOnly(ctx, rc) {
		return nil, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("this API is served by an OpenAI provider with id %q, and none is configured for this key", openAIProviderID),
		}
	}
	rc.Provider = openAIProviderID
	return p, nil
}

// resolveAssistantsProvider resolves the provider for an Assistants API
// request and applies the key's ACL before the provider is called, in chat's
// order and with chat's refusals, on the model the request names, rc.Model
// (an assistant's model, or a run's model override): a request a license
// authorized is held to the "internal" access group, as on chat
// (applyLicenseInternalGroup), then its access groups (keyAccessGroups,
// which may resolve an alias in rc.Model), a model OpenAI does not serve
// (assistantsModel; chat's answer for a model no provider serves), its
// allowed providers (keyProviderACL), then its allowed models and access
// groups (keyModelACL).
func (h *Handlers) resolveAssistantsProvider(ctx context.Context, rc *models.RequestContext) (providers.AssistantsProvider, error) {
	applyLicenseInternalGroup(ctx, rc)
	if apiErr := h.keyAccessGroups(rc); apiErr != nil {
		return nil, apiErr
	}
	if _, ok := assistantsModel(rc.Model); rc.Model != "" && !ok {
		return nil, errModelNotAvailable(rc.Model)
	}
	provider, err := h.resolveOpenAIProvider(ctx, rc)
	if err != nil {
		return nil, err
	}
	if apiErr := h.keyProviderACL(rc); apiErr != nil {
		return nil, apiErr
	}
	if apiErr := h.keyModelACL(rc); apiErr != nil {
		return nil, apiErr
	}

	ap, ok := provider.(providers.AssistantsProvider)
	if !ok {
		return nil, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support the Assistants API", rc.Provider),
		}
	}
	return ap, nil
}

// ---------------------------------------------------------------------------
// Helper: common request setup for Assistants endpoints
// ---------------------------------------------------------------------------

func (h *Handlers) setupAssistantsRC(r *http.Request, operation string) *models.RequestContext {
	return h.setupPassedThroughRC(r, "assistants", operation)
}

// ---------------------------------------------------------------------------
// Helper: proxy non-streaming Assistants request
// ---------------------------------------------------------------------------

// proxyAssistantsNonStream sends the request to the provider through the
// pipeline (runPassedThrough) and passes its answer back as it is. view,
// for a body the checks on the request read, is that body in the chat
// shape; nil for a body they do not read, or none.
func (h *Handlers) proxyAssistantsNonStream(w http.ResponseWriter, r *http.Request, ctx context.Context, rc *models.RequestContext, ap providers.AssistantsProvider, method, upstreamPath string, body []byte, view *requestview.View, queryParams url.Values) {
	var respBody []byte
	var respHeaders http.Header
	statusCode := 0
	err := h.runPassedThrough(ctx, r, rc, body, view, func(ctx context.Context, send []byte) (int, error) {
		var callErr error
		respBody, statusCode, respHeaders, callErr = ap.ProxyAssistantsRequest(ctx, method, upstreamPath, send, queryParams)
		if callErr != nil {
			return 0, callErr
		}
		if statusCode == 0 {
			statusCode = http.StatusOK
		}
		return statusCode, nil
	})
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	if statusCode == 0 {
		models.WriteError(w, errNoPassedThroughAnswer())
		return
	}

	h.setAgentccHeaders(w, rc)

	// Forward upstream headers.
	if respHeaders != nil {
		for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
			if v := respHeaders.Get(key); v != "" {
				w.Header().Set(key, v)
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	w.Write(respBody)
}

// ---------------------------------------------------------------------------
// Helper: proxy streaming Assistants request (SSE relay)
// ---------------------------------------------------------------------------

// proxyAssistantsStream opens the stream through the pipeline
// (runPassedThrough) and relays it. The response plugins (the rate limits'
// settlement, the request log) run once the stream has ended, whether it
// ended or the client went away.
func (h *Handlers) proxyAssistantsStream(w http.ResponseWriter, r *http.Request, ctx context.Context, rc *models.RequestContext, ap providers.AssistantsProvider, upstreamPath string, body []byte, view *requestview.View) {
	var stream io.ReadCloser
	var respHeaders http.Header
	err := h.runPassedThrough(ctx, r, rc, body, view, func(ctx context.Context, send []byte) (int, error) {
		s, _, hdr, callErr := ap.StreamAssistantsRequest(ctx, "POST", upstreamPath, send, nil)
		if callErr != nil {
			return 0, callErr
		}
		stream, respHeaders = s, hdr
		return http.StatusOK, nil
	})
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	if stream == nil {
		models.WriteError(w, errNoPassedThroughAnswer())
		return
	}
	defer stream.Close()
	// The pipeline skips the response plugins of a stream; they run here,
	// after the relay, with a context the client's leaving does not cancel.
	defer h.engine.RunPostPlugins(context.WithoutCancel(ctx), rc)

	h.setAgentccHeaders(w, rc)

	// Forward upstream headers.
	if respHeaders != nil {
		for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
			if v := respHeaders.Get(key); v != "" {
				w.Header().Set(key, v)
			}
		}
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported by server", "request_id", rc.RequestID)
		return
	}
	flusher.Flush()

	// Line-by-line relay with selective event inspection.
	scanner := bufio.NewScanner(stream)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Text()

		// Write the line and newline to the client.
		if _, writeErr := fmt.Fprintf(w, "%s\n", line); writeErr != nil {
			slog.Warn("error writing assistants stream", "request_id", rc.RequestID, "error", writeErr)
			return
		}

		// Flush on empty lines (SSE event separator).
		if line == "" {
			flusher.Flush()
		}

		// Check for context cancellation.
		select {
		case <-ctx.Done():
			return
		default:
		}
	}

	if err := scanner.Err(); err != nil {
		if err != io.EOF {
			slog.Warn("error reading assistants stream", "request_id", rc.RequestID, "error", err)
		}
	}
}

// ---------------------------------------------------------------------------
// Helper: read and validate request body
// ---------------------------------------------------------------------------

func (h *Handlers) readAssistantsBody(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return nil, false
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return nil, false
	}
	return body, true
}

// readAssistantsFields reads a body that may name a model or ask for a
// stream as OpenAI reads it (models.ParseAssistantsBody). A body chat
// refuses is refused here with chat's answer, before the provider is
// resolved.
func readAssistantsFields(w http.ResponseWriter, body []byte) (*models.AssistantsBody, bool) {
	fields, err := models.ParseAssistantsBody(body)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return nil, false
	}
	return fields, true
}

// assistantsSendBody is the body sent for fields: with the model the key's
// ACL was checked on (rc.Model, which an access group's alias may have
// resolved), without an "openai/" prefix, as the only "model". A model
// OpenAI does not serve is refused here too, as resolveAssistantsProvider
// refuses it, so no other name is ever sent in its place.
func assistantsSendBody(w http.ResponseWriter, rc *models.RequestContext, fields *models.AssistantsBody) ([]byte, bool) {
	send, ok := assistantsModel(rc.Model)
	if rc.Model != "" && !ok {
		models.WriteError(w, errModelNotAvailable(rc.Model))
		return nil, false
	}
	body, err := fields.WithModel(send)
	if err != nil {
		models.WriteError(w, models.ErrInvalidAssistantsBody(err.Error()))
		return nil, false
	}
	return body, true
}

// ===========================================================================
// Assistants CRUD: POST /v1/assistants, GET /v1/assistants, etc.
// ===========================================================================

// CreateAssistant handles POST /v1/assistants.
func (h *Handlers) CreateAssistant(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "assistants.create")
	defer rc.Release()

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	// The assistant's model, which the key's allowed models and access
	// groups are checked against as on chat (resolveAssistantsProvider
	// reads rc.Model).
	fields, ok := readAssistantsFields(w, body)
	if !ok {
		return
	}
	rc.Model = fields.Model
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if body, ok = assistantsSendBody(w, rc, fields); !ok {
		return
	}
	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/assistants", body, requestview.Assistant(body), nil)
}

// ListAssistants handles GET /v1/assistants.
func (h *Handlers) ListAssistants(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "assistants.list")
	defer rc.Release()

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Forward pagination query params.
	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/assistants", nil, nil, qp)
}

// GetAssistant handles GET /v1/assistants/{assistant_id}.
func (h *Handlers) GetAssistant(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "assistants.get")
	defer rc.Release()

	assistantID := r.URL.Query().Get("assistant_id")
	if assistantID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "assistant_id is required"))
		return
	}
	rc.Metadata["assistant_id"] = assistantID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/assistants/"+assistantID, nil, nil, nil)
}

// UpdateAssistant handles POST /v1/assistants/{assistant_id}.
func (h *Handlers) UpdateAssistant(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "assistants.update")
	defer rc.Release()

	assistantID := r.URL.Query().Get("assistant_id")
	if assistantID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "assistant_id is required"))
		return
	}
	rc.Metadata["assistant_id"] = assistantID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	// The assistant's new model, checked against the key's allowed models
	// and access groups as on chat.
	fields, ok := readAssistantsFields(w, body)
	if !ok {
		return
	}
	rc.Model = fields.Model
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if body, ok = assistantsSendBody(w, rc, fields); !ok {
		return
	}
	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/assistants/"+assistantID, body, requestview.Assistant(body), nil)
}

// DeleteAssistant handles DELETE /v1/assistants/{assistant_id}.
func (h *Handlers) DeleteAssistant(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "assistants.delete")
	defer rc.Release()

	assistantID := r.URL.Query().Get("assistant_id")
	if assistantID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "assistant_id is required"))
		return
	}
	rc.Metadata["assistant_id"] = assistantID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "DELETE", "/v1/assistants/"+assistantID, nil, nil, nil)
}

// ===========================================================================
// Threads CRUD: POST /v1/threads, GET/POST/DELETE /v1/threads/{thread_id}
// ===========================================================================

// CreateThread handles POST /v1/threads.
func (h *Handlers) CreateThread(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.create")
	defer rc.Release()

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads", body, requestview.Thread(body), nil)
}

// GetThread handles GET /v1/threads/{thread_id}.
func (h *Handlers) GetThread(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.get")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID, nil, nil, nil)
}

// UpdateThread handles POST /v1/threads/{thread_id}.
func (h *Handlers) UpdateThread(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.update")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/"+threadID, body, nil, nil)
}

// DeleteThread handles DELETE /v1/threads/{thread_id}.
func (h *Handlers) DeleteThread(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.delete")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "DELETE", "/v1/threads/"+threadID, nil, nil, nil)
}

// ===========================================================================
// Messages CRUD
// ===========================================================================

// CreateMessage handles POST /v1/threads/{thread_id}/messages.
func (h *Handlers) CreateMessage(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.messages.create")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/messages", body, requestview.ThreadMessage(body), nil)
}

// ListMessages handles GET /v1/threads/{thread_id}/messages.
func (h *Handlers) ListMessages(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.messages.list")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Forward pagination and filter query params.
	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before", "run_id"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/messages", nil, nil, qp)
}

// GetMessage handles GET /v1/threads/{thread_id}/messages/{message_id}.
func (h *Handlers) GetMessage(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.messages.get")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	messageID := r.URL.Query().Get("message_id")
	if threadID == "" || messageID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and message_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["message_id"] = messageID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/messages/"+messageID, nil, nil, nil)
}

// UpdateMessage handles POST /v1/threads/{thread_id}/messages/{message_id}.
func (h *Handlers) UpdateMessage(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.messages.update")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	messageID := r.URL.Query().Get("message_id")
	if threadID == "" || messageID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and message_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["message_id"] = messageID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/messages/"+messageID, body, nil, nil)
}

// DeleteMessage handles DELETE /v1/threads/{thread_id}/messages/{message_id}.
func (h *Handlers) DeleteMessage(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.messages.delete")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	messageID := r.URL.Query().Get("message_id")
	if threadID == "" || messageID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and message_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["message_id"] = messageID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "DELETE", "/v1/threads/"+threadID+"/messages/"+messageID, nil, nil, nil)
}

// ===========================================================================
// Runs
// ===========================================================================

// CreateRun handles POST /v1/threads/{thread_id}/runs.
func (h *Handlers) CreateRun(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.create")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	// The run's model override, checked against the key's allowed models
	// and access groups as on chat. A run that names no model runs on its
	// assistant's stored model, which is checked when the assistant is
	// created or updated through the gateway (keyModelACL).
	fields, ok := readAssistantsFields(w, body)
	if !ok {
		return
	}
	rc.Model = fields.Model
	rc.IsStream = fields.Stream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if body, ok = assistantsSendBody(w, rc, fields); !ok {
		return
	}

	upstreamPath := "/v1/threads/" + threadID + "/runs"

	if fields.Stream {
		h.proxyAssistantsStream(w, r, ctx, rc, ap, upstreamPath, body, requestview.Run(body))
	} else {
		h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", upstreamPath, body, requestview.Run(body), nil)
	}
}

// ListRuns handles GET /v1/threads/{thread_id}/runs.
func (h *Handlers) ListRuns(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.list")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	if threadID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id is required"))
		return
	}
	rc.Metadata["thread_id"] = threadID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs", nil, nil, qp)
}

// GetRun handles GET /v1/threads/{thread_id}/runs/{run_id}.
func (h *Handlers) GetRun(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.get")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	if threadID == "" || runID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and run_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID, nil, nil, nil)
}

// UpdateRun handles POST /v1/threads/{thread_id}/runs/{run_id}.
func (h *Handlers) UpdateRun(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.update")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	if threadID == "" || runID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and run_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/runs/"+runID, body, nil, nil)
}

// CancelRun handles POST /v1/threads/{thread_id}/runs/{run_id}/cancel.
func (h *Handlers) CancelRun(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.cancel")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	if threadID == "" || runID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and run_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/runs/"+runID+"/cancel", nil, nil, nil)
}

// ===========================================================================
// Run Steps
// ===========================================================================

// ListRunSteps handles GET /v1/threads/{thread_id}/runs/{run_id}/steps.
func (h *Handlers) ListRunSteps(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.steps.list")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	if threadID == "" || runID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and run_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	qp := url.Values{}
	for _, key := range []string{"limit", "order", "after", "before"} {
		if v := r.URL.Query().Get(key); v != "" {
			qp.Set(key, v)
		}
	}
	// Forward include[] params.
	if includes := r.URL.Query()["include[]"]; len(includes) > 0 {
		for _, inc := range includes {
			qp.Add("include[]", inc)
		}
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID+"/steps", nil, nil, qp)
}

// GetRunStep handles GET /v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}.
func (h *Handlers) GetRunStep(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.steps.get")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	stepID := r.URL.Query().Get("step_id")
	if threadID == "" || runID == "" || stepID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id, run_id, and step_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID
	rc.Metadata["step_id"] = stepID

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	qp := url.Values{}
	if includes := r.URL.Query()["include[]"]; len(includes) > 0 {
		for _, inc := range includes {
			qp.Add("include[]", inc)
		}
	}

	h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID+"/steps/"+stepID, nil, nil, qp)
}

// ===========================================================================
// Convenience endpoints
// ===========================================================================

// CreateThreadAndRun handles POST /v1/threads/runs.
func (h *Handlers) CreateThreadAndRun(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.create_and_run")
	defer rc.Release()

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	// The run's model override, checked against the key's allowed models
	// and access groups as on chat (a run that names none: CreateRun).
	fields, ok := readAssistantsFields(w, body)
	if !ok {
		return
	}
	rc.Model = fields.Model
	rc.IsStream = fields.Stream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if body, ok = assistantsSendBody(w, rc, fields); !ok {
		return
	}

	if fields.Stream {
		h.proxyAssistantsStream(w, r, ctx, rc, ap, "/v1/threads/runs", body, requestview.ThreadAndRun(body))
	} else {
		h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", "/v1/threads/runs", body, requestview.ThreadAndRun(body), nil)
	}
}

// SubmitToolOutputs handles POST /v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs.
func (h *Handlers) SubmitToolOutputs(w http.ResponseWriter, r *http.Request) {
	rc := h.setupAssistantsRC(r, "threads.runs.submit_tool_outputs")
	defer rc.Release()

	threadID := r.URL.Query().Get("thread_id")
	runID := r.URL.Query().Get("run_id")
	if threadID == "" || runID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_id", "thread_id and run_id are required"))
		return
	}
	rc.Metadata["thread_id"] = threadID
	rc.Metadata["run_id"] = runID

	body, ok := h.readAssistantsBody(w, r)
	if !ok {
		return
	}

	// Whether the outputs' run streams on, read as OpenAI reads it.
	fields, ok := readAssistantsFields(w, body)
	if !ok {
		return
	}
	rc.IsStream = fields.Stream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	upstreamPath := "/v1/threads/" + threadID + "/runs/" + runID + "/submit_tool_outputs"

	if fields.Stream {
		h.proxyAssistantsStream(w, r, ctx, rc, ap, upstreamPath, body, requestview.ToolOutputs(body))
	} else {
		h.proxyAssistantsNonStream(w, r, ctx, rc, ap, "POST", upstreamPath, body, requestview.ToolOutputs(body), nil)
	}
}

// ---------------------------------------------------------------------------
// assistantsModel: the model name an Assistants request sends.
// ---------------------------------------------------------------------------

// assistantsModel is the name sent for model, the name the key's ACL was
// checked on, and whether OpenAI, the only provider these APIs go to, can
// serve it. An "openai/" prefix, in any letter case, is stripped, as chat
// strips a prefix that names the provider it routes to (orgModelMatches).
// Any other name is sent as named. ok is false for a name that still has a
// "/" (another prefix, as in "gpt-4o-mini/gpt-4o" or "anthropic/claude-x":
// no OpenAI model id has one) or is empty ("openai/" alone, or no model).
// Such a name is refused, not stripped, so the model sent is always the
// model the key's ACL checked.
func assistantsModel(model string) (string, bool) {
	if n := len(openAIProviderID) + 1; len(model) >= n && strings.EqualFold(model[:n], openAIProviderID+"/") {
		model = model[n:]
	}
	return model, model != "" && !strings.Contains(model, "/")
}
