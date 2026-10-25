package mcp

// hookResolveMetaTool is step 5 of the tools/call chain (chain.go).
const hookResolveMetaTool = "resolveMetaTool"

// resolveMetaTool is step 5 of the tools/call chain and is a no-op by
// default. Keep observeHook as its first statement.
func resolveMetaTool(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveMetaTool)
	return nil
}
