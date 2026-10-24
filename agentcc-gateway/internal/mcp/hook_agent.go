package mcp

// hookParseAgent is step 2 of every chain (chain.go).
const hookParseAgent = "parseAgent"

// parseAgent is step 2 of every chain and is a no-op by default. Keep
// observeHook as its first statement.
func parseAgent(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookParseAgent)
	return nil
}
