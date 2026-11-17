package main

// The node's own budgets (budgets: in the gateway's config) count every
// organization's traffic on the node. A node that serves two organizations
// is restarted: its org, key and user levels after the start-up seed equal
// what they counted before, the spend of both organizations summed, not the
// spend of whichever organization the seed visited last.

import (
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func nodeBudgetsConfig() *config.Config {
	cfg := config.DefaultConfig()
	cfg.Budgets = config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		Org:           &config.BudgetLevelConfig{Limit: 100},
		Keys:          map[string]config.BudgetLevelConfig{"shared-key": {Limit: 100}},
		Users:         map[string]config.BudgetLevelConfig{"user-1": {Limit: 100}},
	}
	return cfg
}

func TestNodeBudgetsSurviveARestartOfANodeServingTwoOrganizations(t *testing.T) {
	cfg := nodeBudgetsConfig()

	// Before the restart: org X spends 1 on the shared key as user-1 and 2
	// more; org Y spends 2 on the shared key and 2 more as user-1.
	before := budgetpkg.NewTracker(cfg.Budgets)
	before.RecordSpendUsers("", []string{"user-1"}, "shared-key", "m", nil, 1)
	before.RecordSpendUsers("", nil, "", "m", nil, 2)
	before.RecordSpendUsers("", nil, "shared-key", "m", nil, 2)
	before.RecordSpendUsers("", []string{"user-1"}, "", "m", nil, 2)

	// What the control plane recorded for the same requests, per org.
	summary := &tenant.SpendSummary{Period: "monthly", Orgs: map[string]tenant.OrgSpendSummary{
		"org-x": {TotalSpend: 3, PerKey: map[string]float64{"shared-key": 1}, PerUser: map[string]float64{"user-1": 1}, PerModel: map[string]float64{"m": 3}},
		"org-y": {TotalSpend: 4, PerKey: map[string]float64{"shared-key": 2}, PerUser: map[string]float64{"user-1": 2}, PerModel: map[string]float64{"m": 4}},
	}}

	// The restart: a new tracker, seeded at start-up.
	tenants := tenant.NewStore()
	after := budgetpkg.NewTracker(cfg.Budgets)
	plugin := budgetplugin.New(after, budgetplugin.NewPricingLookup(nil), true, tenants)
	seedSummary(cfg, tenants, plugin, after, nil, "monthly", summary)

	for _, c := range []struct{ level, key string }{
		{"org", ""}, {"key", "shared-key"}, {"user", "user-1"},
	} {
		want, wantModels := before.GetSpend(c.level, c.key)
		got, gotModels := after.GetSpend(c.level, c.key)
		if got != want {
			t.Errorf("node %s %q after the restart = %v, want %v as before it", c.level, c.key, got, want)
		}
		if c.level == "org" && gotModels["m"] != wantModels["m"] {
			t.Errorf("node org model m after the restart = %v, want %v", gotModels["m"], wantModels["m"])
		}
	}
}
