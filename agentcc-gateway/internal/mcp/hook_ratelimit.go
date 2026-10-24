package mcp

// hookCheckRateLimit is step 12 of the tools/call chain (chain.go).
const hookCheckRateLimit = "checkRateLimit"

// checkRateLimit is step 12 of the tools/call chain and is a no-op by
// default. Keep observeHook as its first statement.
func checkRateLimit(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckRateLimit)
	return nil
}

// noRateLimit is Deps.RateLimiter's default: no limit.
type noRateLimit struct{}

func (noRateLimit) Admit(*CallContext) (func(), *Denial) { return func() {}, nil }
