package mcp

// hookCheckDepth is step 11 of the tools/call chain (chain.go).
const hookCheckDepth = "checkDepth"

// checkDepth refuses a call nested deeper than the configured agent depth.
//
// Default: the check of the X-Agentcc-Agent-Depth header.
// Refusal: tool_call_depth_exceeded.
// Keep observeHook as its first statement.
func checkDepth(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckDepth)
	if cc.req == nil {
		return nil
	}
	depth := s.depth.ExtractDepth(cc.req)
	if err := s.depth.CheckDepth(depth); err != nil {
		d := rpcDenial(ErrCodeInvalidRequest, "tool_call_depth_exceeded", err.Error())
		d.Metadata = map[string]any{"max_depth": s.depth.MaxDepth()}
		return d
	}
	return nil
}
