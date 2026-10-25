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

// scanResult runs the guardrails on the upstream result (Deps.Scanner).
//
// Default: the server's guard post-check. Refusal: tool_result_blocked.
// Keep observeHook as its first statement.
func scanResult(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanResult)
	return s.Deps().Scanner.ScanResult(cc)
}

// scanResource is a resources/read step and is a no-op by default. Keep
// observeHook as its first statement.
func scanResource(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanResource)
	return nil
}

// scanPrompt is a prompts/get step and is a no-op by default. Keep
// observeHook as its first statement.
func scanPrompt(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanPrompt)
	return nil
}

// identityShaper is Deps.ResultShaper's default: results pass unchanged and
// nothing is spilled.
type identityShaper struct{}

func (identityShaper) Shape(_ *CallContext, result json.RawMessage) json.RawMessage { return result }
func (identityShaper) Read(*CallContext, string) (json.RawMessage, bool)            { return nil, false }
