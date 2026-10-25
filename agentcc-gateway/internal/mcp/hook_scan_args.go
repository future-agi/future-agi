package mcp

import (
	"encoding/json"
	"log/slog"
)

// hookScanArgs is step 16 of the tools/call chain (chain.go).
const hookScanArgs = "scanArgs"

// scanArgs runs the guardrails on the call's arguments before it is
// dispatched (Deps.Scanner).
//
// Default: the server's guard pre-check. Refusal: tool_call_blocked.
// Keep observeHook as its first statement.
func scanArgs(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanArgs)
	return s.Deps().Scanner.ScanArgs(cc)
}

// guardScanner is the default Scanner: the server's ToolCallGuard (SetGuard), read
// at call time, run before and after the upstream call. It does not scan
// listings.
type guardScanner struct{ s *Server }

func (g guardScanner) ScanArgs(cc *CallContext) *Denial {
	if g.s.guard == nil || cc.legacy.tool == nil {
		return nil
	}
	if errMsg := g.s.guard.CheckPre(cc.Tool.ExposedName, cc.Upstream.ServerID, cc.legacy.arguments); errMsg != "" {
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
