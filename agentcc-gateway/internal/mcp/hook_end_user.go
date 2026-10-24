package mcp

// hookResolveEndUser is step 12a of the tools/call chain (chain.go).
const hookResolveEndUser = "resolveEndUser"

// resolveEndUser is step 12a of the tools/call chain and is a no-op by
// default. Keep observeHook as its first statement.
func resolveEndUser(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveEndUser)
	return nil
}
