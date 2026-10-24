package mcp

// hookEvidenceMeta is step 20 of the tools/call chain (chain.go).
const hookEvidenceMeta = "evidenceMeta"

// evidenceMeta is step 20 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func evidenceMeta(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookEvidenceMeta)
	return nil
}
