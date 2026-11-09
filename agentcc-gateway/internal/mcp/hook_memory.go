package mcp

// hookScopeMemory is step 7a of the tools/call chain (chain.go).
const hookScopeMemory = "scopeMemory"

// scopeMemory is filled by V15 to scope a memory tool's call to its subject
// through Deps.Memory, refusing with memory_unscoped_write or
// memory_cross_subject.
// Until then it does nothing. Keep observeHook as its first statement.
func scopeMemory(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookScopeMemory)
	return nil
}

// noMemoryScope is Deps.Memory's default: no memory scoping.
type noMemoryScope struct{}

func (noMemoryScope) Scope(*CallContext) *Denial { return nil }
