package mcp

// hookApplyTerms is step 18a of the tools/call chain (chain.go).
const hookApplyTerms = "applyTerms"

// applyTerms is step 18a of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func applyTerms(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookApplyTerms)
	return nil
}

// noTerms is Deps.Terms' default: no terms constrain a call.
type noTerms struct{}

func (noTerms) Resolve(*CallContext) Terms { return Terms{} }
