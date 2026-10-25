package mcp

import "github.com/futureagi/agentcc-gateway/internal/models"

// Steps 14 and 19 (credits) of the tools/call chain (chain.go).
const (
	hookReserveCredits = "reserveCredits"
	hookSettleCredits  = "settleCredits"
)

// reserveCredits is step 14 of the tools/call chain and is a no-op by
// default. Keep observeHook as its first statement.
func reserveCredits(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReserveCredits)
	return nil
}

// settleCredits is step 19 of the tools/call chain and is a no-op by default.
// Keep observeHook as its first statement.
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
