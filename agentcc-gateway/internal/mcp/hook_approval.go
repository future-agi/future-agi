package mcp

// hookCheckApproval is step 10 of the tools/call chain (chain.go).
const hookCheckApproval = "checkApproval"

// checkApproval is step 10 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func checkApproval(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckApproval)
	return nil
}

// noApprovals is Deps.Approvals' default: no tool needs an approval.
type noApprovals struct{}

func (noApprovals) Check(*CallContext) *Denial { return nil }
