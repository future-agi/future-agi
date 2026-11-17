package mcp

// hookCheckRateLimit is step 12 of the tools/call chain (chain.go).
const hookCheckRateLimit = "checkRateLimit"

// checkRateLimit admits through the shared limiter before credentials or
// upstream dispatch. Its finalizer also releases an optional concurrency slot.
func checkRateLimit(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckRateLimit)
	release, d := s.Deps().RateLimiter.Admit(cc)
	if release != nil {
		cc.Defer("rate-limit", release)
	}
	return d
}

// noRateLimit is Deps.RateLimiter's default: no limit.
type noRateLimit struct{}

func (noRateLimit) Admit(*CallContext) (func(), *Denial) { return func() {}, nil }
