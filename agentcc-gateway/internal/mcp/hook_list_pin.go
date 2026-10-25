package mcp

// hookPinFilter is a tools/list step (chain.go).
const hookPinFilter = "pinFilter"

// pinFilter is a tools/list step and is a no-op by default. Keep observeHook
// as its first statement.
func pinFilter(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookPinFilter)
	return nil
}
