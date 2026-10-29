package server

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
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
// org's providers may not use.
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
			return p, nil
		}
	}

	p, ok := h.registry.GetProvider(openAIProviderID)
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

// resolveAssistantsProvider resolves the provider for an Assistants API request.
func (h *Handlers) resolveAssistantsProvider(ctx context.Context, rc *models.RequestContext) (providers.AssistantsProvider, error) {
	provider, err := h.resolveOpenAIProvider(ctx, rc)
	if err != nil {
		return nil, err
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
	rc := models.AcquireRequestContext()
	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = r.Header.Get("x-agentcc-trace-id")
	rc.EndpointType = "assistants"
	rc.Metadata["assistants_operation"] = operation
	rc.Metadata["client_ip"] = extractClientIP(r)

	setAuthMetadataFromRequest(rc, r)
	// Caller dimensions, from the x-agentcc-metadata header.
	applyCallerMetadata(rc, r, nil)

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

	return rc
}

// ---------------------------------------------------------------------------
// Helper: proxy non-streaming Assistants request
// ---------------------------------------------------------------------------

func (h *Handlers) proxyAssistantsNonStream(w http.ResponseWriter, ctx context.Context, rc *models.RequestContext, ap providers.AssistantsProvider, method, upstreamPath string, body []byte, queryParams url.Values) []byte {
	respBody, statusCode, respHeaders, err := ap.ProxyAssistantsRequest(ctx, method, upstreamPath, body, queryParams)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return nil
	}
	h.writeAssistantsResponse(w, rc, respBody, statusCode, respHeaders)
	return respBody
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

	modelName := models.ExtractModelFromBody(body)
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Replace model name with resolved (strip prefix).
	if modelName != "" && rc.Model != modelName {
		if replaced, replErr := models.ReplaceModelInBody(body, resolveModelNameForAssistants(modelName)); replErr == nil {
			body = replaced
		}
	}

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/assistants", body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/assistants", nil, qp)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/assistants/"+assistantID, nil, nil)
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

	modelName := models.ExtractModelFromBody(body)
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if modelName != "" {
		if replaced, replErr := models.ReplaceModelInBody(body, resolveModelNameForAssistants(modelName)); replErr == nil {
			body = replaced
		}
	}

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/assistants/"+assistantID, body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "DELETE", "/v1/assistants/"+assistantID, nil, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads", body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID, nil, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads/"+threadID, body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "DELETE", "/v1/threads/"+threadID, nil, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/messages", body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/messages", nil, qp)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/messages/"+messageID, nil, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/messages/"+messageID, body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "DELETE", "/v1/threads/"+threadID+"/messages/"+messageID, nil, nil)
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

	modelName := models.ExtractModelFromBody(body)
	// The name as requested, before the provider prefix is stripped: key
	// model lists are checked against it.
	rc.Model = modelName
	isStream := models.ExtractStreamFromBody(body)
	rc.IsStream = isStream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if modelName != "" {
		if replaced, replErr := models.ReplaceModelInBody(body, resolveModelNameForAssistants(modelName)); replErr == nil {
			body = replaced
		}
	}

	h.proxyRunCall(w, ctx, rc, ap, "/v1/threads/"+threadID+"/runs", body, true)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs", nil, qp)
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

	respBody := h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID, nil, nil)
	// A poll stays outside the pipeline, unless it is the first to see the
	// run finish: then it charges the run, as the call that started it.
	if respBody != nil && h.observeRun(ctx, rc, respBody, false) {
		h.engine.RunPostPlugins(ctx, rc)
	}
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/runs/"+runID, body, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "POST", "/v1/threads/"+threadID+"/runs/"+runID+"/cancel", nil, nil)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID+"/steps", nil, qp)
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

	h.proxyAssistantsNonStream(w, ctx, rc, ap, "GET", "/v1/threads/"+threadID+"/runs/"+runID+"/steps/"+stepID, nil, qp)
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

	modelName := models.ExtractModelFromBody(body)
	// The name as requested, before the provider prefix is stripped: key
	// model lists are checked against it.
	rc.Model = modelName
	isStream := models.ExtractStreamFromBody(body)
	rc.IsStream = isStream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if modelName != "" {
		if replaced, replErr := models.ReplaceModelInBody(body, resolveModelNameForAssistants(modelName)); replErr == nil {
			body = replaced
		}
	}

	h.proxyRunCall(w, ctx, rc, ap, "/v1/threads/runs", body, true)
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

	isStream := models.ExtractStreamFromBody(body)
	rc.IsStream = isStream

	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()

	ap, err := h.resolveAssistantsProvider(ctx, rc)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	// Continues a run started earlier, which holds the pending charge.
	h.proxyRunCall(w, ctx, rc, ap, "/v1/threads/"+threadID+"/runs/"+runID+"/submit_tool_outputs", body, false)
}

// ---------------------------------------------------------------------------
// resolveModelNameForAssistants strips provider prefix from model name.
// ---------------------------------------------------------------------------

func resolveModelNameForAssistants(model string) string {
	if idx := strings.Index(model, "/"); idx > 0 {
		return model[idx+1:]
	}
	return model
}
