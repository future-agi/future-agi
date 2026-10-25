package server

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The chat request's hooks run in a fixed order, the same for non-stream,
// stream and failover requests:
//
//  1. runtimeIdentity, then peekKeyOrgID
//  2. resolveOrgConfig
//  3. parseAgentContext (reads body session_id before step 6 strips it)
//  4. effectiveOrgConfig, then the per-org overrides from its result
//  5. resolveContentMode
//  6. applyRoutingOptions
//  7. applyAgentContext
//  8. expandPreset
//  9. idempotencyBegin
//  10. runBudgetCap
//  11. resolveRoute: checkEligibility for an explicit route, buildAutoPool
//     (checkEligibility per candidate) for auto
//  12. pipeline pre-plugins, in priority order (credits 510, then the run
//     budget 515)
//  13. hedgePlan
//  14. per attempt: beginAttempt, then its finish; per fallback hop first
//     fallbackHopAllowed, then billing.ReserveAdd (admitFallbackHop)
//  15. rewriteUpstreamRouting, immediately before each dispatch
//  16. pipeline post-plugins (cost, credits settle, run-budget settle,
//     outcomes)
//  17. checkToolCalls on the final non-stream response (after settlement, so
//     a blocked response is still billed)
//  18. idempotencyFinish
//  19. setAgentccHeaders, which applies rc.SetResponseHeader's buffer
//  20. recordGeneration (async)
//  21. rc.RunFinalizers, deferred by every public handler
//
// A stream writes its headers (18, 19) when its first chunk arrives, before
// its post-plugins (16) and generation record (20) at the end of the stream.
// TestHookOrderNonStream, TestHookOrderStream and TestHookOrderFailover pin
// the order. Steps 1 to 10 run in chatPreRoute, 17 to 20 in
// writeChatResponse.

// Hook names, as reported to the hook observer.
const (
	hookRuntimeIdentity      = "runtimeIdentity"
	hookPeekKeyOrgID         = "peekKeyOrgID"
	hookResolveOrgConfig     = "resolveOrgConfig"
	hookParseAgentContext    = "parseAgentContext"
	hookEffectiveOrgConfig   = "effectiveOrgConfig"
	hookResolveContentMode   = "resolveContentMode"
	hookApplyRoutingOptions  = "applyRoutingOptions"
	hookApplyAgentContext    = "applyAgentContext"
	hookExpandPreset         = "expandPreset"
	hookIdempotencyBegin     = "idempotencyBegin"
	hookRunBudgetCap         = "runBudgetCap"
	hookCheckEligibility     = "checkEligibility"
	hookHedgePlan            = "hedgePlan"
	hookBeginAttempt         = "beginAttempt"
	hookFinishAttempt        = "finishAttempt"
	hookFallbackHopAllowed   = "fallbackHopAllowed"
	hookReserveAdd           = "billing.ReserveAdd"
	hookRewriteUpstream      = "rewriteUpstreamRouting"
	hookCheckToolCalls       = "checkToolCalls"
	hookCheckStreamToolCalls = "checkStreamToolCalls"
	hookIdempotencyFinish    = "idempotencyFinish"
	hookSetAgentccHeaders    = "setAgentccHeaders"
	hookRecordGeneration     = "recordGeneration"
)

// hookTrace is the hook observer, embedded in Handlers: a test seam that
// sees every hook as it runs, with a detail (the attempt kind, provider and
// model where a hook acts on one) and the request (nil for recordGeneration,
// which has a snapshot). It is nil in a running gateway. Every hook reports
// itself first; any change to a hook keeps that report, so the
// order tests keep proving the order.
type hookTrace struct {
	hookObserver func(hook, detail string, rc *models.RequestContext)
}

func (h *Handlers) observeHook(hook string, rc *models.RequestContext) {
	if h.hookObserver != nil {
		h.hookObserver(hook, "", rc)
	}
}

// observeHookAt reports a hook that acts on model at providerID.
func (h *Handlers) observeHookAt(hook string, rc *models.RequestContext, providerID, model string) {
	if h.hookObserver != nil {
		h.hookObserver(hook, providerID+" "+model, rc)
	}
}

// chatPreRoute runs steps 1 to 10 of a chat request and returns the org
// config routing uses: the effective one, after a staged rollout picked its
// variant. When it returns false the response has been written.
func (h *Handlers) chatPreRoute(w http.ResponseWriter, r *http.Request, rc *models.RequestContext, req *models.ChatCompletionRequest, body []byte) (*tenant.OrgConfig, bool) {
	if apiErr := h.runtimeIdentity(rc); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	// Early org_id extraction: the auth plugin has not run yet, but
	// resolveOrgConfig needs key_org_id.
	h.peekKeyOrgID(rc)
	h.observeHook(hookPeekKeyOrgID, rc)

	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.observeHook(hookResolveOrgConfig, rc)

	if apiErr := h.parseAgentContext(rc, r, req); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	orgCfg = h.effectiveOrgConfig(rc, orgCfg)
	h.applyOrgOverrides(orgCfg, rc)
	h.resolveContentMode(rc, orgCfg)
	if apiErr := h.applyRoutingOptions(rc, req); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	if apiErr := h.applyAgentContext(rc, orgCfg, req); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	if apiErr := h.expandPreset(rc, orgCfg, req); apiErr != nil {
		models.WriteError(w, apiErr)
		return nil, false
	}
	if h.idempotencyBegin(w, r, rc, body) {
		return nil, false
	}
	if apiErr := h.runBudgetCap(rc, orgCfg); apiErr != nil {
		h.failChat(w, rc, apiErr)
		return nil, false
	}
	return orgCfg, true
}

// failChat writes err as the response to a chat request that has passed
// idempotencyBegin, after telling the idempotency hook how it ended.
func (h *Handlers) failChat(w http.ResponseWriter, rc *models.RequestContext, err error) {
	h.idempotencyFinish(rc, errorStatus(err), w.Header(), nil)
	models.WriteErrorFromError(w, err)
}

// errorStatus is the HTTP status WriteErrorFromError writes for err.
func errorStatus(err error) int {
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return apiErr.Status
	}
	return http.StatusInternalServerError
}

// writeChatResponse ends a chat request that produced rc.Response, after its
// post-plugins ran: steps 17 to 20. gatewayHeaders is false only for a
// stream request a pre-plugin answered from cache, which has never carried
// the gateway's headers. served is the provider instance that produced the
// response, nil when none did; x-agentcc-timeout-ms reads its client timeout.
func (h *Handlers) writeChatResponse(w http.ResponseWriter, rc *models.RequestContext, gatewayHeaders bool, served providers.Provider) {
	presentUsageCost(rc)
	if apiErr := h.checkToolCalls(rc, rc.Response); apiErr != nil {
		h.failChat(w, rc, apiErr)
		return
	}
	// The body is rendered up front only when the idempotency hook keeps it.
	var body []byte
	if rc.Billing.IdempotencyKey != "" {
		if b, err := json.Marshal(rc.Response); err == nil {
			body = append(b, '\n') // as json.Encoder writes it
		}
	}
	h.idempotencyFinish(rc, http.StatusOK, w.Header(), body)
	if gatewayHeaders {
		h.setAgentccHeadersForProvider(w, rc, served)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if body != nil {
		_, _ = w.Write(body)
	} else {
		_ = json.NewEncoder(w).Encode(rc.Response)
	}
	h.recordGenerationFor(rc)
}
