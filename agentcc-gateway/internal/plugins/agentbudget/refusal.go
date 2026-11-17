package agentbudget

import "github.com/futureagi/agentcc-gateway/internal/models"

// unqualified preserves the run-budget dialect without echoing caller fields.
func (st *runState) unqualified() *models.APIError {
	err := ErrCallCapUnenforceable("", "")
	err.Message = "strict admission requires a qualified request and run scope"
	return st.strict.Refusal(err, "", "")
}

// budgetExhausted attributes only the effective administrative ceiling to the
// admission policy. The scope can retain a tighter ceiling from a caller in an
// earlier request; that remains a legacy run-budget refusal, even if this call
// also has an administrative run policy. Equal ceilings enforce both limits.
func (st *runState) budgetExhausted(required, available, effectiveLimit int64) *models.APIError {
	err := ErrBudgetExhausted(st.scope, required, available)
	if st.strict != nil && st.strict.RunLimit() >= 0 && effectiveLimit == st.strict.RunLimit() {
		return st.strict.RunRefusal(err)
	}
	return err
}
