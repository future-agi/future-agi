package mcp

// hookCheckPin is step 9 of the tools/call chain (chain.go).
const hookCheckPin = "checkPin"

// checkPin is step 9 of the tools/call chain and is a no-op by default. Keep
// observeHook as its first statement.
func checkPin(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckPin)
	return nil
}

// noPins is Deps.Pins' default: nothing pinned, nothing quarantined.
type noPins struct{}

func (noPins) CheckCall(*CallContext) *Denial                 { return nil }
func (noPins) FilterList(_ *CallContext, tools []Tool) []Tool { return tools }
