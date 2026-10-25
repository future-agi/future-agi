package mcp

// hookStartSpan is step 0 of every chain (chain.go).
const hookStartSpan = "startSpan"

// startSpan is step 0 of every chain and is a no-op by default. Keep
// observeHook as its first statement.
func startSpan(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookStartSpan)
	return nil
}

// noTracer is Deps.Tracer's default: no spans.
type noTracer struct{}

func (noTracer) Start(*CallContext) func() { return func() {} }
