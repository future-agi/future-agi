package mcp

// hookScanDescriptions is a tools/list step (chain.go).
const hookScanDescriptions = "scanDescriptions"

// scanDescriptions is filled by the scanner to drop tools whose descriptions the
// guardrails block (Deps.Scanner).
// Until then it does nothing. Keep observeHook as its first statement.
func scanDescriptions(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanDescriptions)
	return nil
}
