package vendors_test

import (
	"slices"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type budgetOperation struct {
	kind   string
	amount int64
}

// Monetary holders share one spy: ANY hold, settlement or void violates BYOK.
// Run/tool-count holders each have their own spy; their hooks still run on BYOK.
type ledger struct {
	mu    sync.Mutex
	calls []budgetOperation
}

func (l *ledger) record(kind string, amount int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.calls = append(l.calls, budgetOperation{kind, amount})
}
func (l *ledger) Hold(_ *mcp.CallContext, amount int64) (mcp.BudgetHold, *mcp.Denial) {
	l.record("hold", amount)
	return l, nil
}
func (l *ledger) Settle(amount int64) { l.record("settle", amount) }
func (l *ledger) Void()               { l.record("void", 0) }
func (l *ledger) snapshot() []budgetOperation {
	l.mu.Lock()
	defer l.mu.Unlock()
	return slices.Clone(l.calls)
}

func (f *fixture) assertBYOKBudgets(t *testing.T, dispatched bool) {
	t.Helper()
	// Do not conflate these non-dollar hooks with charging. Assert each holder's
	// complete sequence, so a missing hook or an extra/incorrect finalizer fails.
	equal(t, f.ledger.snapshot(), []budgetOperation(nil))
	var want []budgetOperation
	if dispatched {
		want = []budgetOperation{{"hold", 0}, {"settle", 0}}
	}
	equal(t, f.runBudget.snapshot(), want)
	equal(t, f.toolBudget.snapshot(), want)
}
