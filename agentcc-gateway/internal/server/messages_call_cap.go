package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// messagesCallCapFitter is the run-budget plugin as chat uses it: CallCap sets
// the call's cap (runBudgetCap, call-order step 10), and ProcessRequest fits
// the call under it (pre-plugin 515): max_tokens clamped to fit, or 402
// agent_call_cap_exceeded.
type messagesCallCapFitter interface {
	RunBudget
	ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult
}

// messagesCallCap applies chat's per-call cost limit to /v1/messages before
// anything is dispatched or held: the caller's max-cost-usd,
// budget-remaining-usd and budget-usd (x-agentcc-*, x-omega-* or
// metadata.agent / metadata.omega) and, on a routed request, the org's
// managed_models.max_call_cost_usd. A request that sends none of them, under an
// org that sets no per-call limit, is untouched.
//
// The agent context is read into a private view of rc, never rc itself, so the
// request's own identity, rate limits and logs stay as Messages always had
// them. Messages spend is never held on or settled into a run, so a request
// under a run ceiling that applies is refused first (messagesRunCeiling); then
// the view drops the run scope and only per-call terms are applied here.
//
// native is the pass-through to an Anthropic-native provider: its bytes go out
// as written, so nothing can be fitted or clamped, and a request that sends a
// per-call limit of its own is refused before dispatch instead of being served
// past it.
func (h *Handlers) messagesCallCap(ctx context.Context, r *http.Request, rc *models.RequestContext, body []byte, orgCfg *tenant.OrgConfig, native bool) *models.APIError {
	orgCap := !native && orgCfg.ManagedPolicy().MaxCallCostUSD != nil
	carrier := messagesAgentCarrier(body)
	view := rc.CloneRequest()
	defer view.Release()
	if apiErr := h.parseAgentContext(view, r, carrier); apiErr != nil {
		// A malformed field beside a run must not skip the run's ceiling.
		if orgCap || messagesSendsCallCap(r, carrier) || messagesNamesRun(r, carrier) {
			return apiErr
		}
		return nil // no limit to apply: Messages ignores agent context as before
	}
	if refusal := h.messagesRunCeiling(ctx, view, orgCfg); refusal != nil {
		return refusal
	}
	a := &view.Agent
	caller := a.MaxCostUSD != nil || a.BudgetRemainingUSD != nil || a.BudgetUSD != nil
	if native {
		if caller {
			return capbudget.CapUnenforceable("A per-call cost limit (x-agentcc-max-cost-usd, budget-remaining-usd or budget-usd) " +
				"cannot be enforced on a native Anthropic pass-through. Send the request without the limit, " +
				"or use /v1/chat/completions to keep it.")
		}
		return nil
	}
	if !caller && !orgCap {
		return nil
	}
	rb, ok := runBudgets.Load(h)
	if !ok {
		return nil // as chat: with no run budget installed no per-call limit is read
	}
	fitter, ok := rb.(messagesCallCapFitter)
	if !ok {
		return capbudget.CapUnenforceable("This gateway cannot enforce a per-call cost limit on /v1/messages.")
	}
	// Per-call terms only: no run scope, so the plugin reads and holds nothing
	// on a run (skipStore).
	a.RunID, a.BudgetScope, view.Runtime = "", "", nil
	// Priced and fitted as chat is, on the translated request itself, so a
	// clamp of max_tokens is what the provider (and the credits hold) sees.
	view.EndpointType = "chat"
	view.Request = rc.Request
	if apiErr := fitter.CallCap(view, orgCfg); apiErr != nil {
		return apiErr
	}
	if res := fitter.ProcessRequest(ctx, view); res.Error != nil {
		return res.Error
	}
	rc.Billing.CallCapUSD = view.Billing.CallCapUSD
	for _, hdr := range view.ResponseHeaders() {
		rc.SetResponseHeader(hdr.Name, hdr.Value)
	}
	return nil
}

// messagesRunCeiling refuses, before dispatch, a Messages request that falls
// under a run ceiling that applies: Messages spend is never held on or settled
// into a run, so serving the request would spend past the ceiling uncounted.
// view carries the parsed agent context. The ceilings: a budget the caller
// sends with its run (budget-usd, or a runtime token's budget), the org's
// routing.agents.max_run_usd under admission caps (where the central guard
// refuses Messages before this handler), and a limit an earlier call stored
// on the run. A stopped run is refused as chat refuses it. A request that
// names no run, or a run under none of these, is untouched: callers that send
// a run id without a limit are served as before.
func (h *Handlers) messagesRunCeiling(ctx context.Context, view *models.RequestContext, orgCfg *tenant.OrgConfig) *models.APIError {
	scope := messagesRunScope(view)
	if scope == "" {
		return nil // budget-usd with no run covers this call alone: a per-call term
	}
	if view.Agent.BudgetUSD != nil || (view.Runtime != nil && view.Runtime.BudgetUSD != nil) {
		return errRunBudgetUnenforceable()
	}
	if orgCfg.AdmissionCapsOn() && orgCfg.Routing != nil && orgCfg.Routing.Agents != nil && orgCfg.Routing.Agents.MaxRunUSD != nil {
		return errRunBudgetUnenforceable()
	}
	rb, ok := runBudgets.Load(h)
	if !ok {
		return nil // as chat: with no run budget installed no run is read
	}
	reader, ok := rb.(interface{ Scopes() *agentbudget.Scopes })
	if !ok {
		return nil
	}
	scopes := reader.Scopes()
	if scopes == nil {
		return nil // no store: no limit can be stored on a run
	}
	org := messagesRunOrg(view)
	stored, err := scopes.Peek(ctx, org, scope)
	if err != nil {
		// As chat: the run's state unknown is a refusal unless the org opts
		// into budgets.fail_mode=open.
		if orgCfg.BudgetsFailMode() == "open" {
			slog.Warn("run budget unavailable; serving the Messages call unchecked (budgets.fail_mode=open)",
				"request_id", view.RequestID, "org_id", org, "scope", scope, "error", err)
			return nil
		}
		return agentbudget.ErrBudgetStateUnavailable(scope)
	}
	if stored.Stopped {
		return agentbudget.ErrRunStopped(scope, stored.StopReason)
	}
	if stored.HasLimit() {
		return errRunBudgetUnenforceable()
	}
	return nil
}

// errRunBudgetUnenforceable is the plain 402 for a Messages request under a
// run ceiling (no metadata; its code is on the Anthropic envelope).
func errRunBudgetUnenforceable() *models.APIError {
	return &models.APIError{Status: http.StatusPaymentRequired, Type: models.ErrTypeInvalidRequest,
		Code: anthropicfmt.CodeRunBudgetUnenforceable,
		Message: "Run budgets are not available on the Messages endpoint (/v1/messages): this request is in a run that has a budget, " +
			"and Messages spend cannot be counted against it. Send calls in a budgeted run to /v1/chat/completions."}
}

// messagesRunScope is the run a request is in, by the run budget's precedence
// (agentbudget scopeOf): a runtime token's budget scope or root run, then
// budget-scope (alias ledger-id), then run-id; "" when it names none.
func messagesRunScope(rc *models.RequestContext) string {
	if rt := rc.Runtime; rt != nil {
		if rt.BudgetScope != "" {
			return rt.BudgetScope
		}
		if rt.RootRunID != "" {
			return rt.RootRunID
		}
	}
	if rc.Agent.BudgetScope != "" {
		return rc.Agent.BudgetScope
	}
	return rc.Agent.RunID
}

// messagesRunOrg is the org a run is kept under, as the run budget keys it
// (agentbudget orgIDOf): the org, else the key's org, else "_".
func messagesRunOrg(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	if id := rc.Metadata["key_org_id"]; id != "" {
		return id
	}
	return "_"
}

// messagesNamesRun reports whether the request names a run (run-id,
// budget-scope or its alias ledger-id) in a header or the body's agent
// context, valid or not.
func messagesNamesRun(r *http.Request, carrier *models.ChatCompletionRequest) bool {
	var in agentInput
	in.scanHeaders(r.Header)
	_ = in.readBody(carrier)
	for ns := nsAgentcc; ns <= nsOmega; ns++ {
		for _, f := range [...]int{models.AgentFieldRunID, models.AgentFieldBudgetScope, slotLedgerID} {
			if in.hdr[ns][f] != "" || in.body[ns][f] != "" {
				return true
			}
		}
	}
	return false
}

// messagesAgentCarrier is the part of a Messages body parseAgentContext reads:
// metadata (its agent and omega objects) and session_id. The Messages
// translator drops both, so they are read from the original bytes.
func messagesAgentCarrier(body []byte) *models.ChatCompletionRequest {
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

// messagesSendsCallCap reports whether the request names a per-call limit of
// its own in a header or the body's agent context, valid or not.
func messagesSendsCallCap(r *http.Request, carrier *models.ChatCompletionRequest) bool {
	var in agentInput
	in.scanHeaders(r.Header)
	_ = in.readBody(carrier)
	for ns := nsAgentcc; ns <= nsOmega; ns++ {
		for _, f := range [...]int{models.AgentFieldMaxCostUSD, models.AgentFieldBudgetRemainingUSD, models.AgentFieldBudgetUSD} {
			if in.hdr[ns][f] != "" || in.body[ns][f] != "" {
				return true
			}
		}
	}
	return false
}
