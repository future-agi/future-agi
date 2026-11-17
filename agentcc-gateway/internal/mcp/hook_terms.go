package mcp

// hookApplyTerms is step 18a of the tools/call chain (chain.go).
const hookApplyTerms = "applyTerms"

// applyTerms is filled by V20 to set cc.Terms from the vendor terms
// (Deps.Terms), which the record and the spans honour.
// Until then it does nothing. Keep observeHook as its first statement.
func applyTerms(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookApplyTerms)
	return nil
}

// noTerms is Deps.Terms' default: no terms constrain a call.
type noTerms struct{}

func (noTerms) Resolve(*CallContext) Terms { return Terms{} }
