package mcp

// hookPriceMeta is a tools/list step (chain.go).
const hookPriceMeta = "priceMeta"

// priceMeta is a tools/list step and is a no-op by default. Keep observeHook
// as its first statement.
func priceMeta(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookPriceMeta)
	return nil
}
