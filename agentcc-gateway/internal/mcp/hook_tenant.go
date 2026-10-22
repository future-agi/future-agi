package mcp

// hookResolveTenant is step 3 of every chain (chain.go).
const hookResolveTenant = "resolveTenant"

// resolveTenant is step 3 of every chain and is a no-op by default. Keep
// observeHook as its first statement.
func resolveTenant(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveTenant)
	return nil
}

// noManagedServers is Deps.ManagedServers' default: no managed servers.
type noManagedServers struct{}

func (noManagedServers) ForOrg(string) []ClientConfig { return nil }
