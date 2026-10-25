package tenant

import "strings"

// Accessors for the budgets section: what each level does past its
// limit, its throttle rate, and the fraction of the limit at which it warns.

// Budget actions past a limit.
const (
	BudgetActionWarn     = "warn"
	BudgetActionBlock    = "block"
	BudgetActionThrottle = "throttle"
)

// DefaultBudgetThrottleRPM is throttle_rpm when a throttle level sets none.
const DefaultBudgetThrottleRPM = 1

// DefaultBudgetWarnThreshold is warn_threshold when the org sets none or one
// outside (0, 1].
const DefaultBudgetWarnThreshold = 0.8

// OrgBudgetAction is what the org budget (org_limit) does past its limit:
// "throttle" when its action says so, else "block" with hard_limit, else
// "warn".
func (b *BudgetsConfig) OrgBudgetAction() string {
	if b == nil {
		return BudgetActionWarn
	}
	return budgetAction(b.Action, b.HardLimit)
}

// OrgThrottleRPM is the org budget's throttle rate, at least 1.
func (b *BudgetsConfig) OrgThrottleRPM() int {
	if b == nil {
		return DefaultBudgetThrottleRPM
	}
	return throttleRPM(b.ThrottleRPM)
}

// WarnFraction is the org's warn_threshold, or DefaultBudgetWarnThreshold.
// 1 (Set Budget's Alert Threshold 100%) warns once the limit is reached.
func (b *BudgetsConfig) WarnFraction() float64 {
	if b == nil || b.WarnThreshold <= 0 || b.WarnThreshold > 1 {
		return DefaultBudgetWarnThreshold
	}
	return b.WarnThreshold
}

// LevelAction is what the level does past its limit: "throttle" when its
// action says so, else "block" when hard (nil hard is true), else "warn".
func (b *BudgetLevelConfig) LevelAction() string {
	if b == nil {
		return BudgetActionBlock
	}
	return budgetAction(b.Action, b.Hard == nil || *b.Hard)
}

// LevelThrottleRPM is the level's throttle rate, at least 1.
func (b *BudgetLevelConfig) LevelThrottleRPM() int {
	if b == nil {
		return DefaultBudgetThrottleRPM
	}
	return throttleRPM(b.ThrottleRPM)
}

// LevelWarnFraction is the fraction of the level's limit at which it warns:
// the lowest of the level's own alert_thresholds inside (0, 1] when it sets
// any (Thresholds), else orgDefault.
func (b *BudgetLevelConfig) LevelWarnFraction(orgDefault float64) float64 {
	if b == nil || len(b.AlertThresholds) == 0 {
		return orgDefault
	}
	lowest := 0.0
	for _, f := range b.Thresholds() {
		if f > 0 && f <= 1 && (lowest == 0 || f < lowest) {
			lowest = f
		}
	}
	if lowest == 0 {
		return orgDefault
	}
	return lowest
}

func budgetAction(action string, hard bool) string {
	if strings.EqualFold(strings.TrimSpace(action), BudgetActionThrottle) {
		return BudgetActionThrottle
	}
	if hard {
		return BudgetActionBlock
	}
	return BudgetActionWarn
}

func throttleRPM(n int) int {
	if n < 1 {
		return DefaultBudgetThrottleRPM
	}
	return n
}
