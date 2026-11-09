package mcp

// hookPriceMeta is a tools/list step (chain.go).
const hookPriceMeta = "priceMeta"

// priceMeta is filled by M18 to add each managed tool's price to its _meta.
// Until then it does nothing. Keep observeHook as its first statement.
func priceMeta(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookPriceMeta)
	return nil
}
