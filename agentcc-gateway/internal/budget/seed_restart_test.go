package budget

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// The node's own budgets after a restart: a seed sets a level to the spend
// the control plane recorded, never below what this node has counted since it
// started, and only for a level that counts over the period the seed was read
// for (the node's default period).

func restartTracker() *Tracker {
	return NewTracker(config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		Org:           &config.BudgetLevelConfig{Limit: 100},
		Keys: map[string]config.BudgetLevelConfig{
			"monthly-key": {Limit: 100},
			"daily-key":   {Limit: 100, Period: "daily"},
		},
	})
}

func TestSeedSpendNeverLowersWhatTheNodeCounted(t *testing.T) {
	tr := restartTracker()
	tr.RecordSpend("", "", "", "m", nil, 5)
	tr.SeedSpend("org", "", 3, map[string]float64{"m": 1})
	if got, pm := tr.GetSpend("org", ""); got != 5 || pm["m"] != 5 {
		t.Fatalf("org after a lower seed = %v (m %v), want 5 (m 5)", got, pm["m"])
	}
	tr.SeedSpend("org", "", 8, map[string]float64{"m": 7})
	if got, pm := tr.GetSpend("org", ""); got != 8 || pm["m"] != 7 {
		t.Fatalf("org after a higher seed = %v (m %v), want 8 (m 7)", got, pm["m"])
	}
}

func TestSeedSpendSkipsALevelOnAnotherPeriod(t *testing.T) {
	tr := restartTracker()
	tr.SeedSpend("key", "daily-key", 7, nil)
	if got, _ := tr.GetSpend("key", "daily-key"); got != 0 {
		t.Fatalf("a daily key seeded from the monthly summary = %v, want 0", got)
	}
	tr.SeedSpend("key", "monthly-key", 7, nil)
	if got, _ := tr.GetSpend("key", "monthly-key"); got != 7 {
		t.Fatalf("a monthly key seeded from the monthly summary = %v, want 7", got)
	}
}
