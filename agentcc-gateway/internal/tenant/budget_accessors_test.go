package tenant

import "testing"

func TestBudgetAccessors(t *testing.T) {
	f := false
	tr := true
	cases := []struct {
		name   string
		got    any
		expect any
	}{
		{"nil budgets: warn", (*BudgetsConfig)(nil).OrgBudgetAction(), BudgetActionWarn},
		{"hard_limit: block", (&BudgetsConfig{HardLimit: true}).OrgBudgetAction(), BudgetActionBlock},
		{"action throttle wins over hard_limit", (&BudgetsConfig{HardLimit: true, Action: "Throttle"}).OrgBudgetAction(), BudgetActionThrottle},
		{"soft: warn", (&BudgetsConfig{Action: "warn"}).OrgBudgetAction(), BudgetActionWarn},
		{"org throttle_rpm default", (&BudgetsConfig{}).OrgThrottleRPM(), 1},
		{"org throttle_rpm below 1", (&BudgetsConfig{ThrottleRPM: -3}).OrgThrottleRPM(), 1},
		{"org throttle_rpm", (&BudgetsConfig{ThrottleRPM: 4}).OrgThrottleRPM(), 4},
		{"warn default", (&BudgetsConfig{}).WarnFraction(), 0.8},
		{"warn out of range", (&BudgetsConfig{WarnThreshold: 80}).WarnFraction(), 0.8},
		{"warn set", (&BudgetsConfig{WarnThreshold: 0.5}).WarnFraction(), 0.5},
		{"warn at 100% (the dialog's Alert Threshold 100)", (&BudgetsConfig{WarnThreshold: 1}).WarnFraction(), 1.0},
		{"level hard nil: block", (&BudgetLevelConfig{}).LevelAction(), BudgetActionBlock},
		{"level hard false: warn", (&BudgetLevelConfig{Hard: &f}).LevelAction(), BudgetActionWarn},
		{"level throttle", (&BudgetLevelConfig{Hard: &tr, Action: "throttle"}).LevelAction(), BudgetActionThrottle},
		{"level throttle_rpm default", (&BudgetLevelConfig{}).LevelThrottleRPM(), 1},
		{"level warn falls back", (&BudgetLevelConfig{}).LevelWarnFraction(0.7), 0.7},
		{"level warn lowest own", (&BudgetLevelConfig{AlertThresholds: []float64{0.9, 0.6, 1}}).LevelWarnFraction(0.8), 0.6},
		{"level warn ignores >1", (&BudgetLevelConfig{AlertThresholds: []float64{1.5}}).LevelWarnFraction(0.8), 0.8},
	}
	for _, c := range cases {
		if c.got != c.expect {
			t.Errorf("%s: got %v, want %v", c.name, c.got, c.expect)
		}
	}
}
