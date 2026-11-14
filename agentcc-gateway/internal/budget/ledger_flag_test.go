package budget

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestCheckBudgetReportsLedgerFallback(t *testing.T) {
	local := NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &config.BudgetLevelConfig{Limit: 10}})
	if r := local.CheckBudget("", "", "", "m", nil); r.StoreUnavailable {
		t.Fatal("pod-local tracker reported a ledger outage")
	}
	healthy := ledgerTracker(&fakeLedger{readOK: true, apply: true, ack: true})
	if r := healthy.CheckBudget("", "", "", "m", nil); r.StoreUnavailable || !r.Allowed {
		t.Fatalf("healthy ledger: %+v", r)
	}
	down := ledgerTracker(&fakeLedger{})
	if r := down.CheckBudget("", "", "", "m", nil); !r.StoreUnavailable || !r.Allowed {
		t.Fatalf("unreadable ledger: %+v", r)
	}
}
