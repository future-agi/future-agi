package server

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The refusals of the call-cap gate: a plain 402, before dispatch.
const (
	capGateCallCode    = "call_cap_unenforceable"
	capGateRunCode     = "run_budget_unenforceable"
	capGateCallMessage = "This endpoint can't enforce a per-request cost limit. Remove the limit from this request, or use chat completions or Messages."
	capGateRunMessage  = "This endpoint can't enforce a run budget. Send this request without a run budget, or use chat completions."
)

// capGateRoutes are the endpoints that cannot fit a call under a per-request
// cost limit: the run budget's CallCap runs only on chat (and Messages), and
// its estimate of any other call is 0. The value is true where the run budget
// still counts the call in its run scope (embeddings and rerank: held at 0,
// settled at cost, refused once the run is stopped or spent), so a run budget
// there is left to it, as before. Gemini countTokens, /v1/messages/count_tokens
// and /v1/decisions are not here: none is billed to credits.
var capGateRoutes = map[string]bool{
	"responses":      false,
	"completion":     false,
	"genai_generate": false,
	"genai_stream":   false,
	"genai_embed":    false,
	"image":          false,
	"speech":         false,
	"speech_stream":  false,
	"transcription":  false,
	"translation":    false,
	"embedding":      true,
	"rerank":         true,
}

func capGateCallRefusal() *models.APIError {
	return &models.APIError{Status: http.StatusPaymentRequired, Type: models.ErrTypeInvalidRequest, Code: capGateCallCode, Message: capGateCallMessage}
}

func capGateRunRefusal() *models.APIError {
	return &models.APIError{Status: http.StatusPaymentRequired, Type: models.ErrTypeInvalidRequest, Code: capGateRunCode, Message: capGateRunMessage}
}

// callCapGate refuses, before dispatch, a request on a capGateRoutes endpoint
// that carries a per-request cost limit, or that falls under a run ceiling
// that applies, since the endpoint would serve it past that limit uncounted:
//   - a per-request limit is the caller's max-cost-usd or budget-remaining-usd,
//     a budget-usd with no run id (x-agentcc-*, x-omega-*, metadata.agent or
//     metadata.omega), a runtime token's budget outside a run, and on a call
//     paid from prepaid credit the org's managed_models.max_call_cost_usd;
//   - a run ceiling applies to a call in a run (run id or budget scope) that
//     sends a budget-usd, whose org sets routing.agents.max_run_usd, or whose
//     run already holds a limit in the run store. A run id alone, with no
//     ceiling anywhere, is not refused.
//
// A request that sends none of these is never parsed here and is served
// exactly as before. The agent context is read into a private view of rc,
// never rc itself, so rc.Agent, rate limits and logs are unchanged.
func (h *Handlers) callCapGate(ctx context.Context, r *http.Request, rc *models.RequestContext, body []byte, orgCfg *tenant.OrgConfig) *models.APIError {
	runCounted, gated := capGateRoutes[rc.EndpointType]
	if !gated {
		return nil
	}
	carrier := capGateCarrier(body)
	var in agentInput
	in.scanHeaders(r.Header)
	_ = in.readBody(carrier) // what was sent, valid or not
	sendsCall := in.capGateSends(models.AgentFieldMaxCostUSD, models.AgentFieldBudgetRemainingUSD, models.AgentFieldBudgetUSD)
	sendsRun := in.capGateSends(models.AgentFieldRunID, models.AgentFieldBudgetScope, slotLedgerID)
	orgCall := orgCfg.ManagedPolicy().MaxCallCostUSD != nil && paidFromPrepaidCredit(rc)
	if !sendsCall && !sendsRun && !orgCall && !in.anyAlias && rc.Runtime == nil {
		return nil
	}
	view := rc.CloneRequest()
	defer view.Release()
	if apiErr := h.parseAgentContext(view, r, carrier); apiErr != nil {
		if sendsCall || orgCall || (sendsRun && !runCounted && capGateOrgRunLimit(orgCfg)) {
			return apiErr // chat's 400: a malformed context never serves a limit
		}
		return nil // no limit to apply: the agent context is ignored here, as before
	}
	a := &view.Agent
	scope := a.BudgetScope
	if scope == "" {
		scope = a.RunID
	}
	if orgCall || a.MaxCostUSD != nil || a.BudgetRemainingUSD != nil || (a.BudgetUSD != nil && scope == "") {
		return capGateCallRefusal()
	}
	if scope == "" || runCounted {
		return nil
	}
	if a.BudgetUSD != nil || capGateOrgRunLimit(orgCfg) {
		return capGateRunRefusal()
	}
	return h.capGateStoredRun(ctx, rc, orgCfg, scope)
}

// capGateStoredRun refuses a call in a run whose scope already holds a limit
// in the run store (set by an earlier call's budget). Without a run budget or
// a store no run can hold one. A store that cannot be read fails as the run
// budget fails: closed (503 budget_state_unavailable) unless the org sets
// budgets.fail_mode=open.
func (h *Handlers) capGateStoredRun(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, scope string) *models.APIError {
	rb, ok := runBudgets.Load(h)
	if !ok {
		return nil
	}
	sp, ok := rb.(interface{ Scopes() *agentbudget.Scopes })
	if !ok {
		return nil
	}
	scopes := sp.Scopes()
	if scopes == nil {
		return nil
	}
	org := rc.Metadata["org_id"]
	if org == "" {
		org = rc.Metadata["key_org_id"]
	}
	if org == "" {
		org = "_" // agentbudget's scope key for a call with no org
	}
	st, err := scopes.Peek(ctx, org, scope)
	if err != nil {
		if orgCfg.BudgetsFailMode() == "open" {
			return nil
		}
		return agentbudget.ErrBudgetStateUnavailable(scope)
	}
	if st.HasLimit() {
		return capGateRunRefusal()
	}
	return nil
}

func capGateOrgRunLimit(cfg *tenant.OrgConfig) bool {
	return cfg != nil && cfg.Routing != nil && cfg.Routing.Agents != nil && cfg.Routing.Agents.MaxRunUSD != nil
}

// capGateSends reports whether any of slots was sent, in a header or the
// body, in either namespace.
func (in *agentInput) capGateSends(slots ...int) bool {
	for ns := nsAgentcc; ns <= nsOmega; ns++ {
		for _, s := range slots {
			if in.hdr[ns][s] != "" || in.body[ns][s] != "" {
				return true
			}
		}
	}
	return false
}

// capGateCarrier is the part of a JSON body parseAgentContext reads: metadata
// (its agent and omega objects) and session_id. nil for a body that is not a
// JSON object (a multipart upload).
func capGateCarrier(body []byte) *models.ChatCompletionRequest {
	if len(body) == 0 {
		return nil
	}
	var fields struct {
		Metadata  json.RawMessage `json:"metadata"`
		SessionID json.RawMessage `json:"session_id"`
	}
	if json.Unmarshal(body, &fields) != nil {
		return nil
	}
	extra := map[string]json.RawMessage{}
	if len(fields.Metadata) > 0 {
		extra["metadata"] = fields.Metadata
	}
	if len(fields.SessionID) > 0 {
		extra["session_id"] = fields.SessionID
	}
	return &models.ChatCompletionRequest{Extra: extra}
}

// capGateBatchSubmit refuses a batch submit that sends a per-request cost
// limit header: its items run as chat calls built from their own bodies, and
// the submit's agent headers never reach them, so the limit would be dropped.
func capGateBatchSubmit(r *http.Request) *models.APIError {
	var in agentInput
	in.scanHeaders(r.Header)
	if in.capGateSends(models.AgentFieldMaxCostUSD, models.AgentFieldBudgetRemainingUSD, models.AgentFieldBudgetUSD) {
		return capGateCallRefusal()
	}
	return nil
}

// capGateWriteGenAI writes a gate refusal in the Google error envelope: the
// HTTP status in error.code, and the gateway's code as the ErrorInfo reason.
func capGateWriteGenAI(w http.ResponseWriter, apiErr *models.APIError) {
	models.SetRetryAfter(w, apiErr)
	status := genaifmt.MapStatusToGoogleStatus(apiErr.Status)
	if apiErr.Status == http.StatusPaymentRequired {
		status = "FAILED_PRECONDITION"
	}
	resp := map[string]any{"error": map[string]any{
		"code": apiErr.Status, "message": apiErr.Message, "status": status,
		"details": []any{map[string]any{
			"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": apiErr.Code, "domain": "agentcc-gateway",
		}},
	}}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(apiErr.Status)
	_ = json.NewEncoder(w).Encode(resp)
}
