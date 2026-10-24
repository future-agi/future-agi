package server

import (
	"context"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// The Assistants, Vector Stores and Files APIs: the handler passes the
// request to the provider's own API path for path (Assistants, Vector
// Stores), or serves it from the gateway's file store (Files), and passes
// the answer back as it is. Each request runs the pipeline as a request to
// a model endpoint does (runPassedThrough).

// setupPassedThroughRC is the request context of a request on the API
// endpoint ("assistants", "vector_stores" or "files"): its operation
// (<endpoint>_operation, which callers cannot set), the caller's metadata,
// and the key's org config applied as on the model endpoints.
func (h *Handlers) setupPassedThroughRC(r *http.Request, endpoint, operation string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = r.Header.Get("x-agentcc-trace-id")
	rc.EndpointType = endpoint
	rc.Metadata[endpoint+"_operation"] = operation
	rc.Metadata["client_ip"] = middleware.ClientIP(r)

	setAuthMetadataFromRequest(rc, r)
	// Caller dimensions, from the x-agentcc-metadata header.
	applyCallerMetadata(rc, r, nil)

	// Peek key org_id before resolving org config (same as ChatCompletion).
	// It also records the key's allowed providers and models (recordKeyACL),
	// which the provider ACL reads before the provider is called.
	h.peekKeyOrgID(rc)

	// Resolve per-org config.
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgRoutingOverrides(orgCfg, rc)
	h.applyOrgCacheOverrides(orgCfg, rc)
	// The org's rate_limiting section covers the routes it names (chat
	// completions, completions, Responses, Messages and its token count,
	// generateContent, the speech relays); these APIs are rate-limited as
	// embeddings, images, audio and OCR are: by the key's own
	// rate_limit_rpm and rate_limit_tpm and the gateway's global_rpm, which
	// the rate-limit plugin applies without the org's section
	// (TestRoutesNotCounted, TestPassedThroughRoutesRateLimited).
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

// passedThroughCall serves a request passed through: body is what to send,
// the request's body after the checks on it rewrote it. It reports the
// status the client is answered with, or the error it is refused with.
type passedThroughCall func(ctx context.Context, body []byte) (int, error)

// runPassedThrough runs a request on the Assistants, Vector Stores or Files
// API through the pipeline, as the model endpoints run theirs. The request
// plugins run before call: key authentication (its status, expiry, IP rules
// and allowed models, for a request that names its model on rc.Model), RBAC,
// the request quota, the checks on the request, budgets, rate limits and
// credits; a refusal is returned and call never runs. The response plugins
// run after it (the rate limits' settlement, the request log, audit and
// metrics), except for a stream (rc.IsStream), whose handler runs them once
// the stream has ended. Cost records no price for these APIs.
//
// view, for a body the checks on the request read, is that body in the chat
// shape: X-Guardrail-Policy applies as on chat, a check that would block or
// rewrite a body the gateway cannot read refuses it (request_unavailable),
// and call gets the body with any rewrite of theirs. Without a view, call
// gets body as it is. The status call reports is the request log's
// (models.RecordPassedThroughAnswer).
//
// The answer goes back to the client as the provider sent it, unread, so
// every request is marked as answering with no chat answer
// (models.NoChatAnswer): the view, or, for a request no check reads,
// unreadRequest. The checks on the answer do not read the request, and one
// in block mode records its trigger as logged, the action taken, as on
// embeddings and search.
func (h *Handlers) runPassedThrough(ctx context.Context, r *http.Request, rc *models.RequestContext, body []byte, view *requestview.View, call passedThroughCall) error {
	readGuardrailPolicyHeader(rc, r)
	if view != nil {
		rc.PassThrough = requestview.NoChatAnswerView{View: view}
	} else {
		rc.PassThrough = unreadRequest{}
	}
	return h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		send := body
		if view != nil {
			send = view.Body()
		}
		status, err := call(ctx, send)
		if err != nil {
			return err
		}
		models.RecordPassedThroughAnswer(rc, status, nil, false)
		return nil
	})
}

// unreadRequest is the request of a passed-through route whose request the
// checks do not read (a list, a retrieval, a delete, a cancel, a
// metadata-only update, a vector store's or a file's request): it holds
// nothing for them, as a request with no body does, so a check on the
// request reads no request and none refuses it as unreadable
// (request_unavailable); and it answers with no chat answer
// (models.NoChatAnswer), so a check on the answer in block mode records its
// trigger as logged on the answer served.
type unreadRequest struct{}

// ChatRequest is no request, and no reason it cannot be read.
func (unreadRequest) ChatRequest(context.Context) (*models.ChatCompletionRequest, string) {
	return nil, ""
}

// ApplyChecks has nothing to write: no check read a request.
func (unreadRequest) ApplyChecks() error { return nil }

// NoChatAnswer marks the request (models.NoChatAnswer).
func (unreadRequest) NoChatAnswer() {}

var (
	_ models.PassThroughRequest = unreadRequest{}
	_ models.NoChatAnswer       = unreadRequest{}
)

// errNoPassedThroughAnswer answers a request whose pipeline ended without
// calling the API: no plugin answers these requests in its place.
func errNoPassedThroughAnswer() *models.APIError {
	return models.ErrInternal("no response from the provider")
}
