package mcp

import (
	"encoding/json"
	"fmt"
	"log/slog"
)

// hookScanArgs is step 16 of the tools/call chain (chain.go).
const hookScanArgs = "scanArgs"

// scanArgs runs the guardrails on the call's arguments before it is
// dispatched (Deps.Scanner), after Deps.FlowControl.Before once P9 is in.
//
// Owner: M22. Default: today's guard pre-check. Refusal: tool_call_blocked.
// Keep observeHook as its first statement.
func scanArgs(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanArgs)
	return s.Deps().Scanner.ScanArgs(cc)
}

// guardScanner is today's guard: the server's ToolCallGuard (SetGuard), read
// at call time, run before and after the upstream call. It does not scan
// listings.
type guardScanner struct{ s *Server }

// upstreamToolBlocker is the part of a guard that judges the upstream tool a
// call resolved to (mcpsec.ToolGuard.BlocksUpstreamTool). An exposed name an
// override chose, an account member's, a hash-capped or a normalized one
// cannot be read back to its tool, so the exposed name alone would let a
// blocked tool through (lane M10's IR-9).
type upstreamToolBlocker interface {
	BlocksUpstreamTool(serverID, tool string) bool
}

func (g guardScanner) ScanArgs(cc *CallContext) *Denial {
	if g.s.guard == nil || cc.legacy.tool == nil {
		return nil
	}
	// The resolved upstream tool first, so a blocked call is never counted
	// by the per-tool rate limit in CheckPre; then today's pre-check.
	var errMsg string
	if b, ok := g.s.guard.(upstreamToolBlocker); ok && b.BlocksUpstreamTool(cc.Upstream.ServerID, cc.Tool.UpstreamName) {
		errMsg = fmt.Sprintf("tool %q is blocked", cc.Tool.ExposedName)
	} else {
		errMsg = g.s.guard.CheckPre(cc.Tool.ExposedName, cc.Upstream.ServerID, cc.legacy.arguments)
	}
	if errMsg != "" {
		slog.Warn("mcp tool call blocked (pre)",
			"tool", cc.Tool.ExposedName,
			"server", cc.Upstream.ServerID,
			"reason", errMsg,
		)
		return rpcDenial(ErrCodeInvalidRequest, "tool_call_blocked", "guardrail: "+errMsg)
	}
	return nil
}

func (g guardScanner) ScanResult(cc *CallContext) *Denial {
	if g.s.guard == nil || cc.legacy.toolResult == nil {
		return nil
	}
	if errMsg := g.s.guard.CheckPost(cc.Tool.ExposedName, cc.Upstream.ServerID, cc.legacy.toolResult); errMsg != "" {
		slog.Warn("mcp tool call blocked (post)",
			"tool", cc.Tool.ExposedName,
			"server", cc.Upstream.ServerID,
			"reason", errMsg,
		)
		return rpcDenial(ErrCodeInvalidRequest, "tool_result_blocked", "guardrail: "+errMsg)
	}
	return nil
}

func (guardScanner) ScanList(_ *CallContext, tools []Tool) []Tool { return tools }

// noFlowControl is Deps.FlowControl's default: no flow tracking.
type noFlowControl struct{}

func (noFlowControl) Before(*CallContext) *Denial                 { return nil }
func (noFlowControl) After(*CallContext, json.RawMessage) *Denial { return nil }
