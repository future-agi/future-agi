package mcp

// hookResolveVirtualServer is step 4 of every chain (chain.go).
const hookResolveVirtualServer = "resolveVirtualServer"

// resolveVirtualServer is step 4 of every chain and is a no-op by default.
// Keep observeHook as its first statement.
func resolveVirtualServer(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveVirtualServer)
	return nil
}
