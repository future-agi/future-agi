package mcp

import "github.com/futureagi/agentcc-gateway/internal/models"

// Steps 14 and 19 (credits) of the tools/call chain (chain.go).
const (
	hookReserveCredits = "reserveCredits"
	hookSettleCredits  = "settleCredits"
)

// reserveCredits is filled by MCP billing so that a managed call runs the spend
// guard (Deps.SpendGuard), then the key-limit hold (Deps.KeyLimits), then
// RESERVE (Deps.Credits), each for the same micros, with a finalizer that
// voids the holds on every exit but settlement, refusing with managed_* or
// key_limit_exceeded.
// Until then it does nothing. Keep observeHook as its first statement.
func reserveCredits(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReserveCredits)
	return nil
}

// settleCredits is filled by MCP billing to settle the credit and key-limit holds
// with the billed micros (step 19, the post part of step 14).
// Until then it does nothing. Keep observeHook as its first statement.
func settleCredits(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookSettleCredits)
	return nil
}

// refuseManaged is the default Credits: with no ledger installed a managed
// call is refused as billing unavailable, never served unbilled.
type refuseManaged struct{}

func (refuseManaged) Hold(*CallContext, int64) (BudgetHold, *Denial) {
	return nil, &Denial{
		Type:     models.ErrTypeServer,
		Code:     "managed_billing_unavailable",
		Message:  "managed tool billing is unavailable",
		Metadata: models.PaymentMeta{Reason: models.ReasonRedisUnavailable},
	}
}
