package mcp

import (
	"encoding/json"
)

// Step 18 of the tools/call chain, and the result scans of resources/read and
// prompts/get (chain.go).
const (
	hookScanResult   = "scanResult"
	hookScanResource = "scanResource"
	hookScanPrompt   = "scanPrompt"
)

// scanResult runs the guardrails on the upstream result (Deps.Scanner), then
// Deps.FlowControl.After and Deps.ResultShaper.Shape once flow control and result shaping are in.
//
// Default: today's guard post-check. Refusal: tool_result_blocked.
// Keep observeHook as its first statement.
func scanResult(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanResult)
	return s.Deps().Scanner.ScanResult(cc)
}

// scanResource is filled by the scanner to scan a resources/read result.
// Until then it does nothing. Keep observeHook as its first statement.
func scanResource(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanResource)
	return nil
}

// scanPrompt is filled by the scanner to scan a prompts/get result.
// Until then it does nothing. Keep observeHook as its first statement.
func scanPrompt(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanPrompt)
	return nil
}

// identityShaper is Deps.ResultShaper's default: results pass unchanged and
// nothing is spilled.
type identityShaper struct{}

func (identityShaper) Shape(_ *CallContext, result json.RawMessage) json.RawMessage { return result }
func (identityShaper) Read(*CallContext, string) (json.RawMessage, bool)            { return nil, false }
