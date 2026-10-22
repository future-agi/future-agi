package mcp

import (
	"context"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Step 6 of the tools/call chain, and the access steps of resources/read and
// prompts/get (chain.go).
const (
	hookAuthorizeTool     = "authorizeTool"
	hookAuthorizeResource = "authorizeResource"
	hookAuthorizePrompt   = "authorizePrompt"
)

// authorizeTool resolves the tool the call names and checks that the
// caller's key may use it.
//
// Default: the registry lookup and the per-key allow and deny lists.
// Refusal: unknown tool, tool_denied.
// Keep observeHook as its first statement.
func authorizeTool(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizeTool)
	name := cc.Tool.ExposedName
	regTool, ok := s.registry.ResolveTool(name)
	if !ok {
		return rpcDenial(ErrCodeMethodNotFound, "tool_not_found", "tool not found: "+name)
	}
	// Per-key filtering: check if this tool is allowed for the authenticated key.
	if reason := checkToolKeyAccess(cc.Context(), name); reason != "" {
		d := rpcDenial(ErrCodeInvalidRequest, "tool_denied", reason)
		d.Type = models.ErrTypePermission
		return d
	}
	cc.legacy.tool = regTool
	cc.Upstream.ServerID = regTool.Server
	cc.Tool.UpstreamName = regTool.OrigName
	cc.Tool.Annotations = regTool.Tool.Annotations
	return nil
}

// authorizeResource resolves the resource a resources/read names.
//
// Default: the registry lookup. Refusal: resource not found.
// Keep observeHook as its first statement.
func authorizeResource(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizeResource)
	uri := cc.legacy.resourceURI
	regRes, ok := s.registry.ResolveResource(uri)
	if !ok {
		return rpcDenial(ErrCodeMethodNotFound, "resource_not_found", "resource not found: "+uri)
	}
	cc.legacy.resource = regRes
	cc.Upstream.ServerID = regRes.Server
	return nil
}

// authorizePrompt resolves the prompt a prompts/get names.
//
// Default: the registry lookup. Refusal: prompt not found.
// Keep observeHook as its first statement.
func authorizePrompt(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAuthorizePrompt)
	name := cc.legacy.promptName
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

// filterToolsByKey filters the tool list based on per-key allowed/denied tools in context.
func filterToolsByKey(ctx context.Context, tools []Tool) []Tool {
	allowed, _ := ctx.Value(ctxKeyAllowedTools).([]string)
	denied, _ := ctx.Value(ctxKeyDeniedTools).([]string)

	if len(allowed) == 0 && len(denied) == 0 {
		return tools
	}

	filtered := make([]Tool, 0, len(tools))
	for _, tool := range tools {
		if len(allowed) > 0 && !stringInSlice(tool.Name, allowed) {
			continue
		}
		if len(denied) > 0 && stringInSlice(tool.Name, denied) {
			continue
		}
		filtered = append(filtered, tool)
	}
	return filtered
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

// stringInSlice checks if a string is present in a slice.
func stringInSlice(s string, list []string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}
