package mcp

// hookScanDescriptions is a tools/list step (chain.go).
const hookScanDescriptions = "scanDescriptions"

// scanDescriptions is a tools/list step and is a no-op by default. Keep
// observeHook as its first statement.
func scanDescriptions(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScanDescriptions)
	return nil
}
