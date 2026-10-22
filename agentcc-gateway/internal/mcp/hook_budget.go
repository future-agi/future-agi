package mcp

// Steps 15 and 19 (budget) of the tools/call chain (chain.go).
const (
	hookReserveBudget = "reserveBudget"
	hookSettleBudget  = "settleBudget"
)

// reserveBudget is step 15 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func reserveBudget(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReserveBudget)
	return nil
}

// settleBudget is step 19 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
func settleBudget(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookSettleBudget)
	return nil
}

// noRunGuard is Deps.RunGuard's default: no run is stopped.
type noRunGuard struct{}

func (noRunGuard) Check(*CallContext) *Denial { return nil }
