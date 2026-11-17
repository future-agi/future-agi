package mcp

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// hookParseAgent is step 2 of every chain (chain.go).
const hookParseAgent = "parseAgent"

// stepSourceMethod is agent.step_source when the MCP method sets the step:
// tools/call is always step tool.
const stepSourceMethod = "method"

// parseAgent gives a tools/call its tool_call_id ("tc_" + ULID), reads the
// agent context (agentctx.go: runtime claims, the agent headers, framework
// aliases, then params._meta["io.futureagi/agent"]) into cc.Agent with the
// generation id and traceparent, forces agent.step to tool on tools/call, and
// strips every agent header, alias, inbound credential and io.futureagi/*
// _meta key from what the later hooks and the upstream see.
//
// Refusal: HTTP 400, JSON-RPC -32602, data.code
// agent_header_invalid or agent_header_conflict.
// Keep observeHook as its first statement.
func parseAgent(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookParseAgent)
	if cc.Method == MethodToolsCall && cc.ToolCallID == "" {
		cc.ToolCallID = "tc_" + ulid.Make().String()
	}
	in, d := readAgentInput(cc.Request(), cc.Message())
	captureApprovalRetry(cc)
	if cc.Principal.Kind == PrincipalKey && s != nil && s.search.Load() != nil && cc.searchRequest == nil && cc.req != nil {
		cc.searchRequest = cc.req.Clone(cc.req.Context())
		cc.searchRequest.Header.Del("Cookie")
		cc.searchRequest.Header.Del("Proxy-Authorization")
	}
	stripInbound(cc)
	if d != nil {
		return d
	}
	var rt *models.RuntimeIdentity
	if caller, ok := callerAuthFrom(cc.Context()); ok {
		rt = caller.runtime
	}
	res, d := in.resolve(rt)
	if d != nil {
		return d
	}
	if cc.Method == MethodToolsCall {
		res.agent.Step, res.agent.StepSource = models.StepTool, stepSourceMethod
	}
	cc.Agent, cc.GenerationID, cc.TraceParent = res.agent, res.generationID, res.traceParent
	return nil
}

// inboundCredentialHeaders are the inbound credentials no upstream ever sees.
var inboundCredentialHeaders = map[string]bool{
	"authorization":       true,
	"x-api-key":           true,
	"proxy-authorization": true,
	"cookie":              true,
}

// stripInbound leaves the later hooks and the upstream client no agent header,
// alias, inbound credential or gateway _meta key: it replaces the call's
// request with a clone without them, and its message with one whose
// params._meta has no io.futureagi/* key and no traceparent (the gateway's
// span sets its own). Their values live on in cc.Principal, cc.Agent,
// cc.GenerationID and cc.TraceParent. Everything else stays: the session,
// protocol and Mcp-Param-* headers, the inbound depth header that checkDepth
// reads (the tool budgets decide what an upstream sees of it), the other x-agentcc-*
// headers, the client's own _meta keys and the tool's arguments.
func stripInbound(cc *CallContext) {
	if r := cc.req; r != nil && hasStrippedHeader(r.Header) {
		clone := r.Clone(r.Context())
		for k := range clone.Header {
			if strippedInboundHeader(k) {
				clone.Header.Del(k)
			}
		}
		cc.req = clone
	}
	if m := cc.msg; m != nil {
		if params, changed := stripGatewayMeta(m.Params); changed {
			cp := *m
			cp.Params = params
			cc.msg = &cp
		}
	}
}

func hasStrippedHeader(h http.Header) bool {
	for k := range h {
		if strippedInboundHeader(k) {
			return true
		}
	}
	return false
}

// strippedInboundHeader reports whether an inbound header never goes past
// hook 2: a credential, any x-omega-* header, an x-agentcc-* agent header or
// the generation id, a framework alias (traceparent included), x-session-id.
func strippedInboundHeader(name string) bool {
	n := strings.ToLower(name)
	if inboundCredentialHeaders[n] || strings.HasPrefix(n, omegaHeaderPrefix) {
		return true
	}
	if _, ok := agentHeaderIndex[n]; ok {
		return true
	}
	if _, ok := agentAliasIndex[n]; ok {
		return true
	}
	return n == HeaderGenerationID || n == headerSessionGeneric
}

// stripGatewayMeta returns params without the io.futureagi/* keys and the
// traceparent of its _meta (dropping a _meta left empty), and whether it
// changed anything. Params or a _meta that is not an object is left as sent.
func stripGatewayMeta(params json.RawMessage) (json.RawMessage, bool) {
	meta := paramsMeta(params)
	changed := false
	for k := range meta {
		if strings.HasPrefix(k, metaKeyGatewayPrefix) || k == metaKeyTraceparent {
			delete(meta, k)
			changed = true
		}
	}
	if !changed {
		return params, false
	}
	var p map[string]json.RawMessage
	if json.Unmarshal(params, &p) != nil {
		return params, false
	}
	if len(meta) == 0 {
		delete(p, "_meta")
	} else {
		raw, err := json.Marshal(meta)
		if err != nil {
			return params, false
		}
		p["_meta"] = raw
	}
	out, err := json.Marshal(p)
	if err != nil {
		return params, false
	}
	return out, true
}
