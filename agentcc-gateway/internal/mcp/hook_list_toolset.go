package mcp

// hookToolset is a tools/list step (chain.go).
const hookToolset = "toolset"

// toolset is a tools/list step and is a no-op by default. Keep observeHook as
// its first statement.
func toolset(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookToolset)
	return nil
}
