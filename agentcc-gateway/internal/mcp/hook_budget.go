package mcp

// Steps 15 and 19 (budget) of the tools/call chain (chain.go).
const (
	hookReserveBudget = "reserveBudget"
	hookSettleBudget  = "settleBudget"
)

// reserveBudget checks the trusted run guard, then reserves before dispatch.
// Finalizers settle calls that may have executed, and void all other exits
// (including argument scanning failures and panics before dispatch).
func reserveBudget(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookReserveBudget)
	if d := s.Deps().RunGuard.Check(cc); d != nil {
		return d
	}
	for _, holder := range []Holder{s.Deps().RunBudget, s.Deps().ToolBudget} {
		hold, d := holder.Hold(cc, cc.Billing.ReserveMicros)
		if d != nil {
			return d
		}
		if hold != nil {
			cc.Defer("tool-budget", func() {
				if cc.Dispatched || cc.Executed {
					hold.Settle(cc.Billing.BilledMicros)
				} else {
					hold.Void()
				}
			})
		}
	}
	return nil
}

// settleBudget is filled by M19 to settle the budget holds (step 19, the
// post part of step 15).
// Until then it does nothing. Keep observeHook as its first statement.
func settleBudget(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookSettleBudget)
	return nil
}

// noRunGuard is Deps.RunGuard's default: no run is stopped.
type noRunGuard struct{}

func (noRunGuard) Check(*CallContext) *Denial { return nil }
