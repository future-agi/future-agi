package mcp

// hookScopeMemory is step 7a of the tools/call chain (chain.go).
const hookScopeMemory = "scopeMemory"

// scopeMemory is step 7a of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func scopeMemory(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScopeMemory)
	return nil
}

// noMemoryScope is Deps.Memory's default: no memory scoping.
type noMemoryScope struct{}

func (noMemoryScope) Scope(*CallContext) *Denial { return nil }
