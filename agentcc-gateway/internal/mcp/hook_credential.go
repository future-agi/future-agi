package mcp

// hookResolveCredential is step 13 of the tools/call chain (chain.go).
const hookResolveCredential = "resolveCredential"

// resolveCredential is step 13 of the tools/call chain and is a no-op by
// default: every server uses its configured credential. Keep observeHook as
// its first statement.
func resolveCredential(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveCredential)
	return nil
}

// configCredential is Deps.CredentialResolver's default: each server keeps
// its configured credential.
type configCredential struct{}

func (configCredential) Resolve(*CallContext) (*AuthOverride, *Denial) { return nil, nil }

// noTokenExchange is Deps.TokenExchanger's default: it never applies.
type noTokenExchange struct{}

func (noTokenExchange) Exchange(*CallContext) (*AuthOverride, bool, *Denial) { return nil, false, nil }

// noVendorCredentials is Deps.VendorCredentials' default: the configured
// credential.
type noVendorCredentials struct{}

func (noVendorCredentials) Resolve(*CallContext, string) (*AuthOverride, *Denial) { return nil, nil }
