package budget

import (
	"context"
	"testing"
)

// ShareGlobalBudgets puts the config-file global levels on the shared ledger:
// spend recorded by other replicas or by video counts against the ceiling.
func TestShareGlobalBudgetsReadsSharedLedger(t *testing.T) {
	ledger := &outageLedger{available: true, readOK: true, total: 20}
	p := New(globalTracker(), nil, true, nil)
	p.SetRedisBudget(ledger)
	p.ShareGlobalBudgets()
	chat := outageRequest("chat")
	defer chat.Release()
	r := p.ProcessRequest(context.Background(), chat)
	if r.Error == nil || r.Error.Status != 429 || r.Error.Code != "budget_exceeded" {
		t.Fatalf("shared ledger spend ignored: %+v", r.Error)
	}
	// Without a Redis store there is nothing to share; the call is a no-op.
	local := New(globalTracker(), nil, true, nil)
	local.ShareGlobalBudgets()
	other := outageRequest("chat")
	defer other.Release()
	if r := local.ProcessRequest(context.Background(), other); r.Error != nil {
		t.Fatalf("local-only tracker refused: %+v", r.Error)
	}
}
