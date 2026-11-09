package mcp

// hookEvidenceMeta is step 20 of the tools/call chain (chain.go).
const hookEvidenceMeta = "evidenceMeta"

// toolCallMeta is _meta["io.futureagi/tool_call"] (MC.3): the block every
// tools/call result carries, denials included, which agents and M33's shim
// read.
type toolCallMeta struct {
	ToolCallID  string  `json:"tool_call_id"`
	Executed    bool    `json:"executed"`
	BillingMode string  `json:"billing_mode"`
	BilledUSD   float64 `json:"billed_usd"`
	LatencyMs   int64   `json:"latency_ms"`
}

// evidenceMeta is step 20, after the settles, and runs after a denial too:
// it writes _meta["io.futureagi/tool_call"] {tool_call_id, executed,
// billing_mode, billed_usd, latency_ms} on the result or the denial
// (cc.SetResponseMeta), from cc.Billing (byok and 0 until M18 fills it) and
// cc.Timings. executed means "may have executed": it is true once the call
// was dispatched, its request bytes written to the upstream (cc.Dispatched,
// M13's client), since a dispatched call may have run there even when no
// result came back (a timeout, an HTTP error, a lost answer). A call that
// never left the gateway (a failed dial, a refused connection) says false
// (M15 IR-4, ruled both ways; IC-R1-8). An agent must not blindly retry a
// non-idempotent call that says true. It
// also tells the call's record that cc is answering it now, so the record
// and this block report the same latency, and a retry that adopted a parked
// call is recorded with its own fields.
//
// Owner: M15. Keep observeHook as its first statement.
func evidenceMeta(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookEvidenceMeta)
	now := s.Deps().Clock()
	openRecords.respond(cc, now)
	latency, _ := callLatencies(cc, now)
	cc.SetResponseMeta(MetaKeyToolCall, toolCallMeta{
		ToolCallID:  cc.ToolCallID,
		Executed:    cc.Executed || cc.Dispatched,
		BillingMode: callBillingMode(cc),
		BilledUSD:   float64(cc.Billing.BilledMicros) / 1e6,
		LatencyMs:   latency,
	})
	return nil
}
