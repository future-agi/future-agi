package budget

// Every budget refusal carries the level, period,
// period start and action that refused it, overwriting any caller value, so
// the request log (record.go copies rc.Metadata) lets ingestion emit
// budget.exceeded once per level and period.

import (
	"context"
	"testing"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

var btRefusalKeys = []string{"budget_exceeded_level", "budget_period", "budget_period_start", "budget_action"}

// A caller's own values for the refusal keys, which a refusal must overwrite.
var btForged = map[string]string{
	"budget_exceeded_level": "org", "budget_period": "total",
	"budget_period_start": "1999-01-01T00:00:00Z", "budget_action": "block",
}

func btMerge(a, b map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range a {
		out[k] = v
	}
	for k, v := range b {
		out[k] = v
	}
	return out
}

func TestBudgetRefusalMetadata(t *testing.T) {
	hard := boolPtr(true)
	tiny := func(period string) *tenant.BudgetLevelConfig {
		return &tenant.BudgetLevelConfig{Limit: 0.000001, Period: period, Hard: hard}
	}
	cases := []struct {
		name    string
		budgets *tenant.BudgetsConfig
		who     map[string]string
		code    string
		want    [4]string
	}{
		{"org, default monthly", &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.000001, HardLimit: true},
			nil, "org_budget_exceeded", [4]string{"org", "monthly", "2026-10-01T00:00:00Z", "block"}},
		{"org, its own period", &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.000001, OrgPeriod: "daily", HardLimit: true},
			nil, "org_budget_exceeded", [4]string{"org", "daily", "2026-10-21T00:00:00Z", "block"}},
		{"team, weekly", &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{"eng": tiny("weekly")}},
			map[string]string{"key_team": "eng"}, "org_budget_exceeded", [4]string{"team:eng", "weekly", "2026-10-19T00:00:00Z", "block"}},
		{"user, the org default period", &tenant.BudgetsConfig{Enabled: true, DefaultPeriod: "daily", Users: map[string]*tenant.BudgetLevelConfig{"alice": tiny("")}},
			map[string]string{"auth_key_owner": "alice"}, "org_budget_exceeded", [4]string{"user:alice", "daily", "2026-10-21T00:00:00Z", "block"}},
		{"key, total", &tenant.BudgetsConfig{Enabled: true, Keys: map[string]*tenant.BudgetLevelConfig{"ci": tiny("total")}},
			map[string]string{"auth_key_name": "ci"}, "org_budget_exceeded", [4]string{"key:ci", "total", "0001-01-01T00:00:00Z", "block"}},
		{"tag", &tenant.BudgetsConfig{Enabled: true, Tags: map[string]*tenant.BudgetLevelConfig{"env:prod": tiny("monthly")}},
			map[string]string{"tag:env": "prod"}, "org_budget_exceeded", [4]string{"tag:env:prod", "monthly", "2026-10-01T00:00:00Z", "block"}},
		{"per-model cap of a team", &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{
			"eng": {Limit: 1000, Hard: hard, PerModel: map[string]float64{"gpt-4o": 0.000001}}}},
			map[string]string{"key_team": "eng"}, "org_budget_exceeded", [4]string{"team:eng", "monthly", "2026-10-01T00:00:00Z", "block"}},
		{"throttle", &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.000001, Action: "throttle", ThrottleRPM: 1},
			nil, "budget_throttled", [4]string{"org", "monthly", "2026-10-01T00:00:00Z", "throttle"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p, _ := btPlugin(t, tc.budgets)
			btSpend(t, p, "1.00", tc.who)
			if tc.code == "budget_throttled" {
				if res, _ := btAsk(p, tc.who); res.Error != nil {
					t.Fatalf("first throttle slot refused: %s", res.Error.Code)
				}
			}
			res, rc := btAsk(p, btMerge(tc.who, btForged))
			if btCode(res) != tc.code {
				t.Fatalf("got %s, want %s", btCode(res), tc.code)
			}
			for i, k := range btRefusalKeys {
				if rc.Metadata[k] != tc.want[i] {
					t.Errorf("%s = %q, want %q", k, rc.Metadata[k], tc.want[i])
				}
			}
		})
	}

	t.Run("an allowed request carries none of them", func(t *testing.T) {
		p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, OrgLimit: 100, HardLimit: true})
		_, rc := btAsk(p, nil)
		for _, k := range btRefusalKeys {
			if v, ok := rc.Metadata[k]; ok {
				t.Errorf("allowed request carries %s=%q", k, v)
			}
		}
	})

	t.Run("the operator's config.yaml budgets (budget_exceeded)", func(t *testing.T) {
		tracker := budgetpkg.NewTracker(config.BudgetsConfig{
			Enabled: true, DefaultPeriod: "weekly",
			Teams: map[string]config.BudgetLevelConfig{"eng": {Limit: 0.000001, Hard: boolPtr(true)}},
			Org:   &config.BudgetLevelConfig{Limit: 1000, Period: "daily", Hard: boolPtr(true)},
		})
		p := New(tracker, testPricing(), true, nil)
		p.now = func() time.Time { return btNow }
		eng := map[string]string{"key_team": "eng"}
		btSpend(t, p, "1.00", eng)
		rc := btRC(btMerge(eng, btForged))
		res := p.ProcessRequest(context.Background(), rc)
		if btCode(res) != "budget_exceeded" {
			t.Fatalf("got %s, want budget_exceeded", btCode(res))
		}
		// The node tracker reads the wall clock.
		start := budgetpkg.PeriodStart("weekly", time.Now().UTC()).Format(time.RFC3339)
		want := [4]string{"team:eng", "weekly", start, "block"}
		for i, k := range btRefusalKeys {
			if rc.Metadata[k] != want[i] {
				t.Errorf("%s = %q, want %q", k, rc.Metadata[k], want[i])
			}
		}
	})
}
