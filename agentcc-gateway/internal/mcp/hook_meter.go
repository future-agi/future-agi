package mcp

// hookRegisterRecord is step 2a of the tools/call chain (chain.go).
const hookRegisterRecord = "registerRecord"

// registerRecord is step 2a of the tools/call chain and is a no-op by
// default. Keep observeHook as its first statement.
func registerRecord(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookRegisterRecord)
	return nil
}

// discardToolStats is Deps.ToolStats' default: nothing is observed.
type discardToolStats struct{}

func (discardToolStats) Observe(*CallContext) {}
