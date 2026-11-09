package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Step 6 of the tools/call chain, and the access steps of resources/read and
// prompts/get (chain.go).
const (
	hookAuthorizeTool     = "authorizeTool"
	hookAuthorizeResource = "authorizeResource"
	hookAuthorizePrompt   = "authorizePrompt"
)

// HeaderMCPAllowTools narrows the tools of one request to its globs
// (comma-separated); it can never widen them.
const HeaderMCPAllowTools = "x-agentcc-mcp-allow-tools"

// The ids a tool_denied answer names in metadata.rule; access.rules[i] is
// vserver.RuleID and a tool policy's is "policy:<id>".
const (
	ruleAccessDefault = "access.default"
	ruleHeader        = "header"
	ruleCallback      = "callback"
	ruleKeyAllowed    = "key.mcp_allowed_tools"
	ruleKeyDenied     = "key.mcp_denied_tools"
)

// authorizeTool resolves the tool a call names and checks the caller may use
// it, then runs Deps.ToolPolicy (M10).
//
// A tool the call cannot see answers like one that does not exist: -32602
// "Unknown tool" through a virtual server, today's -32601 on the legacy view.
// A quarantined newcomer called by its own name (no tool holds that exact
// name) answers in band with tool_quarantined {reason: collision}, but only
// to a caller the rules below would let use it; anyone else gets -32602.
// A tool it sees but a later rule refuses answers in band with tool_denied
// and metadata {rule, virtual_server}: access.default, access.rules[i], the
// key's MCP lists, the x-agentcc-mcp-allow-tools header, a callback token's
// allowed_tools, then the tool policy (deny is policy:<id>, approve sets
// cc.PolicyApproval). On the legacy view the key's lists keep today's
// JSON-RPC refusal. Keep observeHook as its first statement.
func authorizeTool(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizeTool)
	name := cc.Tool.ExposedName
	if v := callViewOf(cc); v != nil {
		e, ok := v.tools.Lookup(name)
		if !ok {
			if q, held := v.tools.Quarantined(name); held && v.refusal(cc, q) == "" {
				bindTool(cc, v.byRef[q.Ref()], q.Member.Account)
				return toolQuarantined(name)
			}
			return &Denial{Type: models.ErrTypeInvalidRequest, Code: "tool_not_found", Message: "Unknown tool: " + name, RPCCode: ErrCodeInvalidParams}
		}
		bindTool(cc, v.byRef[e.Ref()], e.Member.Account)
		if rule := v.refusal(cc, e); rule != "" {
			return toolDenied(cc, rule)
		}
	} else {
		regTool, ok := s.registry.ResolveTool(name)
		if !ok {
			return rpcDenial(ErrCodeMethodNotFound, "tool_not_found", "tool not found: "+name)
		}
		bindTool(cc, regTool, "")
		if reason := checkToolKeyAccess(cc.Context(), name); reason != "" {
			d := rpcDenial(ErrCodeInvalidRequest, "tool_denied", reason)
			d.Type = models.ErrTypePermission
			return d
		}
		if rule := requestRefusal(cc, []string{regTool.Server + "/" + regTool.OrigName, name}); rule != "" {
			return toolDenied(cc, rule)
		}
	}
	switch dec := s.Deps().ToolPolicy.Evaluate(cc); dec.Effect {
	case PolicyDeny:
		return toolDenied(cc, "policy:"+dec.RuleID)
	case PolicyApprove:
		cc.PolicyApproval = dec.RuleID
	}
	return nil
}

// bindTool points the call at a resolved tool, for the upstream call, the
// guard scanner and the record.
func bindTool(cc *CallContext, rt *RegisteredTool, account string) {
	cc.legacy.tool = rt
	cc.Upstream.ServerID = rt.Server
	cc.Tool.UpstreamName = rt.OrigName
	cc.Tool.Annotations = rt.Upstream.Annotations
	if account != "" {
		cc.Credential.Ref = account
	}
}

// toolNames are the forms a tool's lists and globs match. A deny matches
// any of them; an allow list an operator or org admin set matches only the
// forms no override can change, so an override never widens what such a
// list lets through.
type toolNames struct {
	stable []string // <member>/<tool>, <server>/<tool>, server<sep>tool, the default exposed name
	all    []string // stable plus the exposed name
}

func (v *callView) names(e vserver.Entry) toolNames {
	stable := append(e.Names(), e.Member.Server+v.sep+e.Name, vserver.EncodePrefixed(e.Member.Key(), e.Name))
	return toolNames{stable: stable, all: append(append([]string(nil), stable...), e.Exposed)}
}

// refusal returns the rule that refuses a tool the view exposes, or "".
func (v *callView) refusal(cc *CallContext, e vserver.Entry) string {
	if v.denyAll != "" {
		return v.denyAll
	}
	n := v.names(e)
	if rule := vserver.DeniedByRules(v.vs.Access.Rules, principalOf(cc), n.stable, n.all); rule != "" {
		return rule
	}
	allowed, _ := cc.Context().Value(ctxKeyAllowedTools).([]string)
	denied, _ := cc.Context().Value(ctxKeyDeniedTools).([]string)
	if len(allowed) > 0 && !anyListed(allowed, n.stable) {
		return ruleKeyAllowed
	}
	if anyListed(denied, n.all) {
		return ruleKeyDenied
	}
	return requestRefusal(cc, n.all)
}

// itemRefusal returns the rule that refuses a member's prompt or resource to
// the caller, or "": access.default on the compatibility view, else the
// access rules, judged as for a tool by <member>/<name> (and <server>/<name>
// for an account member), the deny side also by the exposed name. The key's
// MCP lists, the x-agentcc-mcp-allow-tools header and a callback token's
// allowed_tools name tools only, so they leave prompts and resources alone.
func (v *callView) itemRefusal(cc *CallContext, m vserver.Member, name, exposed string) string {
	if v.denyAll != "" {
		return v.denyAll
	}
	stable := vserver.Tool{Member: m, Name: name}.Names()
	return vserver.DeniedByRules(v.vs.Access.Rules, principalOf(cc), stable, append(append([]string(nil), stable...), exposed))
}

// requestRefusal is the narrowing a request carries in both views: the
// x-agentcc-mcp-allow-tools header, then a callback token's allowed_tools
// (M34), which binds even when empty.
func requestRefusal(cc *CallContext, names []string) string {
	if vserver.Outside(headerAllowTools(cc), names...) {
		return ruleHeader
	}
	p := cc.Principal
	if p.Kind == PrincipalCallback || len(p.AllowedTools) > 0 {
		if !vserver.Any(p.AllowedTools, names...) {
			return ruleCallback
		}
	}
	return ""
}

func headerAllowTools(cc *CallContext) []string {
	r := cc.Request()
	if r == nil {
		return nil
	}
	var globs []string
	for _, v := range r.Header.Values(HeaderMCPAllowTools) {
		for _, g := range strings.Split(v, ",") {
			if g = strings.TrimSpace(g); g != "" {
				globs = append(globs, g)
			}
		}
	}
	return globs
}

// toolDenied is the in-band refusal of a tool the caller can see (MC.3):
// metadata {rule, virtual_server}, with virtual_server only when the call
// has one (a compatibility or legacy view has none; mcp-denial-v1 types it
// as a string, so it is left out rather than sent as null).
func toolDenied(cc *CallContext, rule string) *Denial {
	meta := map[string]any{"rule": rule}
	if cc.VirtualServer != "" {
		meta["virtual_server"] = cc.VirtualServer
	}
	return &Denial{
		Type:     models.ErrTypePermission,
		Code:     "tool_denied",
		Message:  fmt.Sprintf("tool %q is not allowed for this caller", cc.Tool.ExposedName),
		Metadata: meta,
	}
}

// toolQuarantined is the in-band refusal of a quarantined newcomer called by
// its name (MC.3, reason collision): the name is held by an incumbent, so
// the newcomer is never served under it until an admin resolves the
// collision (M21). mcp-denial-v1 allows only reason here: a collision has no
// revisions.
func toolQuarantined(name string) *Denial {
	return &Denial{
		Type:     models.ErrTypePermission,
		Code:     "tool_quarantined",
		Message:  fmt.Sprintf("tool %q is quarantined: its name collides with a tool already exposed", name),
		Metadata: map[string]any{"reason": vserver.ReasonCollision},
	}
}

// authorizeResource resolves the resource a resources/read names: a result
// handle (mcp://fagi/results/<id>) through Deps.ResultShaper, an
// mcp://<member>/<uri> name through the virtual server, else the registry's
// URI. An unknown or hidden one answers by era (resourceNotFound).
// Keep observeHook as its first statement.
func authorizeResource(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizeResource)
	uri := cc.legacy.resourceURI
	if isResultHandle(uri) {
		raw, ok := s.Deps().ResultShaper.Read(cc, uri)
		if !ok {
			return resourceNotFound(cc, uri)
		}
		cc.Result = raw
		return nil
	}
	if v := callViewOf(cc); v != nil {
		m, res, ok := v.resource(uri)
		if !ok || v.itemRefusal(cc, m, res.URI, uri) != "" {
			return resourceNotFound(cc, uri)
		}
		cc.legacy.resource = &RegisteredResource{Server: m.Server, Resource: res}
		cc.legacy.resourceURI = res.URI // the upstream reads its own URI
		cc.Upstream.ServerID = m.Server
		if m.Account != "" {
			cc.Credential.Ref = m.Account
		}
		return nil
	}
	regRes, ok := s.registry.ResolveResource(uri)
	if !ok {
		return resourceNotFound(cc, uri)
	}
	cc.legacy.resource = regRes
	cc.Upstream.ServerID = regRes.Server
	return nil
}

// resourceNotFound is the answer to an unknown or hidden resource: -32602 on
// a 2026-07-28 request, -32002 on a legacy one (the spec moved the code).
func resourceNotFound(cc *CallContext, uri string) *Denial {
	code := errCodeResourceNotFoundLegacy
	if cc.Era == era2026 {
		code = ErrCodeInvalidParams
	}
	data, _ := json.Marshal(map[string]string{"uri": uri})
	return &Denial{Type: models.ErrTypeNotFound, Code: "resource_not_found", Message: "Resource not found", RPCCode: code, Data: data}
}

// authorizePrompt resolves the prompt a prompts/get names: through the
// virtual server (-32602 "Unknown prompt" when it is not there or an access
// rule refuses it, so the two answer alike), else today's registry lookup.
// Keep observeHook as its first statement.
func authorizePrompt(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizePrompt)
	name := cc.legacy.promptName
	if v := callViewOf(cc); v != nil {
		e, ok := v.prompts.Lookup(name)
		if !ok || v.itemRefusal(cc, e.Member, e.Name, e.Exposed) != "" {
			return &Denial{Type: models.ErrTypeInvalidRequest, Code: "prompt_not_found", Message: "Unknown prompt: " + name, RPCCode: ErrCodeInvalidParams}
		}
		// The upstream hook strips server<separator> to get the upstream name.
		cc.legacy.prompt = &RegisteredPrompt{Server: e.Member.Server, Prompt: Prompt{Name: e.Member.Server + s.registry.Separator() + e.Name}}
		cc.Upstream.ServerID = e.Member.Server
		if e.Member.Account != "" {
			cc.Credential.Ref = e.Member.Account
		}
		return nil
	}
	regPrompt, ok := s.registry.ResolvePrompt(name)
	if !ok {
		return rpcDenial(ErrCodeMethodNotFound, "prompt_not_found", "prompt not found: "+name)
	}
	cc.legacy.prompt = regPrompt
	cc.Upstream.ServerID = regPrompt.Server
	return nil
}

// allowAllTools is Deps.ToolPolicy's default: no policy, every call allowed.
type allowAllTools struct{}

func (allowAllTools) Evaluate(*CallContext) PolicyDecision {
	return PolicyDecision{Effect: PolicyAllow}
}

// checkToolKeyAccess checks if a tool is accessible for the authenticated key.
// Returns an error message if blocked, or "" if allowed.
func checkToolKeyAccess(ctx context.Context, toolName string) string {
	allowed, _ := ctx.Value(ctxKeyAllowedTools).([]string)
	denied, _ := ctx.Value(ctxKeyDeniedTools).([]string)

	if len(allowed) > 0 && !stringInSlice(toolName, allowed) {
		return fmt.Sprintf("tool %q not in allowed list for this API key", toolName)
	}
	if len(denied) > 0 && stringInSlice(toolName, denied) {
		return fmt.Sprintf("tool %q is denied for this API key", toolName)
	}
	return ""
}

// anyListed reports whether list names any of names exactly.
func anyListed(list, names []string) bool {
	for _, n := range names {
		if stringInSlice(n, list) {
			return true
		}
	}
	return false
}

// stringInSlice checks if a string is present in a slice.
func stringInSlice(s string, list []string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}
