package mcp

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The MCP request chains. Each hook is
// func(*Server, *CallContext) *Denial in its own hook_<concern>.go file, with
// a no-op or the built-in behaviour as its default, and reports itself with
// observeHook as its first statement. A hook that returns a denial stops the
// chain; only the steps marked always still run, and then the finalizers.
// The order is fixed: a hook changes in its own file, never here.
//
// tools/call:
//
//	0   startSpan (hook_trace.go)
//	1   resolvePrincipal (hook_auth.go)
//	2   parseAgent (hook_agent.go)
//	2a  registerRecord (hook_meter.go)
//	3   resolveTenant (hook_tenant.go)
//	4   resolveVirtualServer (hook_vserver.go)
//	5   resolveMetaTool (hook_metatool.go)
//	6   authorizeTool (hook_acl.go; default: the per-key ACL)
//	7   translateRetry (hook_translate.go)
//	7a  scopeMemory (hook_memory.go)
//	8   validateArgs (hook_validate.go; default: the schema check)
//	9   checkPin (hook_pin.go)
//	10  checkApproval (hook_approval.go)
//	11  checkDepth (hook_depth.go; default: the depth check)
//	12  checkRateLimit (hook_ratelimit.go)
//	12a resolveEndUser (hook_end_user.go)
//	13  resolveCredential (hook_credential.go)
//	14  reserveCredits (hook_billing.go)
//	15  reserveBudget (hook_budget.go)
//	16  scanArgs (hook_scan_args.go; default: the guard pre-check)
//	17  callUpstream (hook_upstream.go; default: client.CallTool)
//	18  scanResult (hook_scan_result.go; default: the guard post-check)
//	18a applyTerms (hook_terms.go)
//	19  settleCredits (hook_billing.go), settleBudget (hook_budget.go)
//	20  evidenceMeta (hook_evidence.go), also after a denial
//	F   finalizers, last in first out (Defer, Park)
//
// A request that resumes a parked call (isParkedRetry) runs only the
// steps marked onRetry: 0 to 4 with 2a, then translateRetry, which adopts the
// parked call, and then 18 onwards with the resumed result.
//
// tools/list runs 0 to 4 (without 2a), then assembleList, pinFilter,
// scanDescriptions, toolset and priceMeta. resources/list and prompts/list
// run 0 to 4, then filterResources or filterPrompts; resources/read and
// prompts/get run 0 to 4, then authorizeResource or authorizePrompt, the
// upstream read and the result scan.

// hookFunc is one chain step.
type hookFunc func(*Server, *CallContext) *Denial

// chainStep is a named hook and when it runs.
type chainStep struct {
	name string
	run  hookFunc
	// onRetry: the step also runs on a request that resumes a parked call.
	onRetry bool
	// always: the step also runs after an earlier step's denial.
	always bool
}

// toolsCallChain is the tools/call order.
var toolsCallChain = []chainStep{
	{name: hookStartSpan, run: startSpan, onRetry: true},
	{name: hookResolvePrincipal, run: resolvePrincipal, onRetry: true},
	{name: hookParseAgent, run: parseAgent, onRetry: true},
	{name: hookRegisterRecord, run: registerRecord, onRetry: true},
	{name: hookResolveTenant, run: resolveTenant, onRetry: true},
	{name: hookResolveVirtualServer, run: resolveVirtualServer, onRetry: true},
	{name: hookResolveMetaTool, run: resolveMetaTool},
	{name: hookAuthorizeTool, run: authorizeTool},
	{name: hookTranslateRetry, run: translateRetry, onRetry: true},
	{name: hookScopeMemory, run: scopeMemory},
	{name: hookValidateArgs, run: validateArgs},
	{name: hookCheckPin, run: checkPin},
	{name: hookCheckApproval, run: checkApproval},
	{name: hookCheckDepth, run: checkDepth},
	{name: hookCheckRateLimit, run: checkRateLimit},
	{name: hookResolveEndUser, run: resolveEndUser},
	{name: hookResolveCredential, run: resolveCredential},
	{name: hookReserveCredits, run: reserveCredits},
	{name: hookReserveBudget, run: reserveBudget},
	{name: hookScanArgs, run: scanArgs},
	{name: hookCallUpstream, run: callUpstream},
	{name: hookScanResult, run: scanResult, onRetry: true},
	{name: hookApplyTerms, run: applyTerms, onRetry: true},
	{name: hookSettleCredits, run: settleCredits, onRetry: true},
	{name: hookSettleBudget, run: settleBudget, onRetry: true},
	{name: hookEvidenceMeta, run: evidenceMeta, onRetry: true, always: true},
}

// requestSteps are steps 0 to 4 without 2a: the head of every other chain.
var requestSteps = []chainStep{
	{name: hookStartSpan, run: startSpan},
	{name: hookResolvePrincipal, run: resolvePrincipal},
	{name: hookParseAgent, run: parseAgent},
	{name: hookResolveTenant, run: resolveTenant},
	{name: hookResolveVirtualServer, run: resolveVirtualServer},
}

// toolsListChain is the tools/list order.
var toolsListChain = withHead(
	chainStep{name: hookAssembleList, run: assembleList},
	chainStep{name: hookPinFilter, run: pinFilter},
	chainStep{name: hookScanDescriptions, run: scanDescriptions},
	chainStep{name: hookToolset, run: toolset},
	chainStep{name: hookPriceMeta, run: priceMeta},
)

var resourcesListChain = withHead(chainStep{name: hookFilterResources, run: filterResources})

var resourcesReadChain = withHead(
	chainStep{name: hookAuthorizeResource, run: authorizeResource},
	chainStep{name: hookReadResource, run: readResource},
	chainStep{name: hookScanResource, run: scanResource},
)

var promptsListChain = withHead(chainStep{name: hookFilterPrompts, run: filterPrompts})

var promptsGetChain = withHead(
	chainStep{name: hookAuthorizePrompt, run: authorizePrompt},
	chainStep{name: hookGetPrompt, run: getPrompt},
	chainStep{name: hookScanPrompt, run: scanPrompt},
)

func withHead(tail ...chainStep) []chainStep {
	return append(append([]chainStep(nil), requestSteps...), tail...)
}

// newCallContext starts the call context of one request.
func (s *Server) newCallContext(r *http.Request, msg *Message, session *Session) *CallContext {
	cc := &CallContext{
		Method:          msg.Method,
		ProtocolVersion: r.Header.Get("MCP-Protocol-Version"),
		ctx:             r.Context(),
		req:             r,
		msg:             msg,
		session:         session,
		observe:         s.hookObserver,
	}
	cc.RequestID = requestID(r)
	if session != nil {
		cc.SessionID = session.ID
	}
	cc.Timings.Started = s.Deps().Clock()
	return cc
}

// runChain runs steps in order. After a denial only the always steps run; on
// a resumed parked call only the onRetry steps do.
func (s *Server) runChain(cc *CallContext, steps []chainStep) {
	for _, st := range steps {
		if cc.Decision != nil && !st.always {
			continue
		}
		if cc.retry && !st.onRetry {
			continue
		}
		if d := st.run(s, cc); d != nil && cc.Decision == nil {
			cc.Decision = d
		}
	}
}

// runToolsCall serves tools/call: the session check, the params, the chain
// and the answer. The finalizers run on every exit, a panic included.
func (s *Server) runToolsCall(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}
	var params ToolCallParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid tool call params")
		return
	}
	cc := s.newCallContext(r, msg, session)
	defer cc.runFinalizers()
	cc.Tool.ExposedName = params.Name
	cc.Args = rawArguments(msg.Params)
	cc.legacy.arguments = params.Arguments
	cc.retry = isParkedRetry(s, cc)
	s.runChain(cc, toolsCallChain)
	s.writeToolCall(w, cc)
}

// runListOrRead serves the other chained methods: the session check, the
// chain, then write, which answers the call or its denial.
func (s *Server) runListOrRead(w http.ResponseWriter, r *http.Request, msg *Message, steps []chainStep, prepare func(http.ResponseWriter, *CallContext) bool, write func(http.ResponseWriter, *CallContext)) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}
	cc := s.newCallContext(r, msg, session)
	defer cc.runFinalizers()
	if prepare != nil && !prepare(w, cc) {
		return
	}
	s.runChain(cc, steps)
	if cc.Decision != nil {
		writeDenialRPC(w, cc)
		return
	}
	write(w, cc)
}

// requestID is the gateway request id the RequestID middleware set.
func requestID(r *http.Request) string {
	return models.GetRequestID(r.Context())
}

// rawArguments returns the arguments member of tools/call params as sent.
func rawArguments(params json.RawMessage) json.RawMessage {
	var p struct {
		Arguments json.RawMessage `json:"arguments"`
	}
	if json.Unmarshal(params, &p) != nil {
		return nil
	}
	return p.Arguments
}
