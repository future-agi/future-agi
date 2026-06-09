package budget

import (
	"strconv"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Tracker manages hierarchical budget tracking across org, team, user, key, and tag levels.
// Thread-safe: each budget level has its own mutex.
type Tracker struct {
	warnThreshold float64
	defaultPeriod string

	org   *levelBudget
	teams map[string]*levelBudget
	users map[string]*levelBudget
	keys  map[string]*levelBudget
	tags  map[string]*levelBudget
}

// levelBudget pairs a budget configuration with a runtime counter.
type levelBudget struct {
	period   string
	limit    float64
	hard     bool
	perModel map[string]float64
	counter  counter
}

// counter tracks spend within a period. Protected by its own mutex.
type counter struct {
	mu          sync.Mutex
	totalSpend  float64
	modelSpend  map[string]float64
	periodStart time.Time
	requests    int64
}

// CheckResult contains the result of a budget check.
type CheckResult struct {
	Allowed   bool
	Remaining float64  // Lowest remaining across all levels
	Warnings  []string // Levels that exceeded warn threshold
	BlockedBy string   // Level:key that blocked (empty if allowed)
	BlockMsg  string   // Human-readable block message
}

// NewTracker creates a Tracker from config.
func NewTracker(cfg config.BudgetsConfig) *Tracker {
	t := &Tracker{
		warnThreshold: cfg.WarnThreshold,
		defaultPeriod: cfg.DefaultPeriod,
		teams:         make(map[string]*levelBudget),
		users:         make(map[string]*levelBudget),
		keys:          make(map[string]*levelBudget),
		tags:          make(map[string]*levelBudget),
	}

	if t.defaultPeriod == "" {
		t.defaultPeriod = "monthly"
	}
	if t.warnThreshold <= 0 || t.warnThreshold >= 1 {
		t.warnThreshold = 0.8
	}

	if cfg.Org != nil {
		t.org = newLevelBudget(*cfg.Org, t.defaultPeriod)
	}
	for name, lc := range cfg.Teams {
		t.teams[name] = newLevelBudget(lc, t.defaultPeriod)
	}
	for name, lc := range cfg.Users {
		t.users[name] = newLevelBudget(lc, t.defaultPeriod)
	}
	for name, lc := range cfg.Keys {
		t.keys[name] = newLevelBudget(lc, t.defaultPeriod)
	}
	for name, lc := range cfg.Tags {
		t.tags[name] = newLevelBudget(lc, t.defaultPeriod)
	}

	return t
}

func newLevelBudget(lc config.BudgetLevelConfig, defaultPeriod string) *levelBudget {
	period := lc.Period
	if period == "" {
		period = defaultPeriod
	}
	hard := true
	if lc.Hard != nil {
		hard = *lc.Hard
	}
	perModel := make(map[string]float64)
	for m, v := range lc.PerModel {
		perModel[m] = v
	}
	return &levelBudget{
		period:   period,
		limit:    lc.Limit,
		hard:     hard,
		perModel: perModel,
		counter: counter{
			modelSpend: make(map[string]float64),
		},
	}
}

// CheckBudget verifies all applicable budgets for a request.
func (t *Tracker) CheckBudget(team, user, keyName, model string, tags map[string]string) CheckResult {
	now := time.Now().UTC()
	result := CheckResult{Allowed: true, Remaining: -1}

	// Check each level.
	t.checkLevel(t.org, "org", "org", model, now, &result)
	if team != "" {
		if lb, ok := t.teams[team]; ok {
			t.checkLevel(lb, "team", team, model, now, &result)
		}
	}
	if user != "" {
		if lb, ok := t.users[user]; ok {
			t.checkLevel(lb, "user", user, model, now, &result)
		}
	}
	if keyName != "" {
		if lb, ok := t.keys[keyName]; ok {
			t.checkLevel(lb, "key", keyName, model, now, &result)
		}
	}
	for k, v := range tags {
		tag := k + ":" + v
		if lb, ok := t.tags[tag]; ok {
			t.checkLevel(lb, "tag", tag, model, now, &result)
		}
	}

	return result
}

func (t *Tracker) checkLevel(lb *levelBudget, level, key, model string, now time.Time, result *CheckResult) {
	if lb == nil {
		return
	}

	lb.counter.mu.Lock()
	defer lb.counter.mu.Unlock()

	lb.counter.maybeReset(lb.period, now)

	// Check total budget.
	remaining := lb.limit - lb.counter.totalSpend
	if remaining < 0 {
		remaining = 0
	}

	// Track lowest remaining.
	if result.Remaining < 0 || remaining < result.Remaining {
		result.Remaining = remaining
	}

	// Check warning threshold.
	if lb.limit > 0 && lb.counter.totalSpend >= lb.limit*t.warnThreshold {
		result.Warnings = append(result.Warnings, level+":"+key)
	}

	// Check hard/soft cap on total.
	if lb.limit > 0 && lb.counter.totalSpend >= lb.limit {
		if lb.hard {
			result.Allowed = false
			result.BlockedBy = level + ":" + key
			result.BlockMsg = "budget exceeded: " + level + " '" + key + "' has spent $" +
				formatUSD(lb.counter.totalSpend) + " of $" + formatUSD(lb.limit) + " " + lb.period + " limit"
		}
	}

	// Check per-model budget.
	if model != "" {
		if modelLimit, ok := lb.perModel[model]; ok && modelLimit > 0 {
			modelSpend := lb.counter.modelSpend[model]
			modelRemaining := modelLimit - modelSpend
			if modelRemaining < 0 {
				modelRemaining = 0
			}
			if modelRemaining < result.Remaining || result.Remaining < 0 {
				result.Remaining = modelRemaining
			}
			if modelSpend >= modelLimit && lb.hard {
				result.Allowed = false
				result.BlockedBy = level + ":" + key + ":model:" + model
				result.BlockMsg = "budget exceeded: " + level + " '" + key + "' has spent $" +
					formatUSD(modelSpend) + " of $" + formatUSD(modelLimit) + " " + lb.period + " limit on model '" + model + "'"
			}
			if modelSpend >= modelLimit*t.warnThreshold {
				result.Warnings = append(result.Warnings, level+":"+key+":model:"+model)
			}
		}
	}
}

// RecordSpend records actual cost against all applicable budgets.
func (t *Tracker) RecordSpend(team, user, keyName, model string, tags map[string]string, cost float64) {
	if cost <= 0 {
		return
	}

	now := time.Now().UTC()

	t.recordLevel(t.org, model, cost, now)
	if team != "" {
		if lb, ok := t.teams[team]; ok {
			t.recordLevel(lb, model, cost, now)
		}
	}
	if user != "" {
		if lb, ok := t.users[user]; ok {
			t.recordLevel(lb, model, cost, now)
		}
	}
	if keyName != "" {
		if lb, ok := t.keys[keyName]; ok {
			t.recordLevel(lb, model, cost, now)
		}
	}
	for k, v := range tags {
		tag := k + ":" + v
		if lb, ok := t.tags[tag]; ok {
			t.recordLevel(lb, model, cost, now)
		}
	}
}

func (t *Tracker) recordLevel(lb *levelBudget, model string, cost float64, now time.Time) {
	if lb == nil {
		return
	}

	lb.counter.mu.Lock()
	defer lb.counter.mu.Unlock()

	lb.counter.maybeReset(lb.period, now)
	lb.counter.totalSpend += cost
	lb.counter.requests++
	if model != "" {
		lb.counter.modelSpend[model] += cost
	}
}

// GetSpend returns the current spend for a specific level and key.
func (t *Tracker) GetSpend(level, key string) (total float64, perModel map[string]float64) {
	lb := t.lookupLevel(level, key)
	if lb == nil {
		return 0, nil
	}

	lb.counter.mu.Lock()
	defer lb.counter.mu.Unlock()

	lb.counter.maybeReset(lb.period, time.Now().UTC())

	pm := make(map[string]float64, len(lb.counter.modelSpend))
	for m, s := range lb.counter.modelSpend {
		pm[m] = s
	}
	return lb.counter.totalSpend, pm
}

func (t *Tracker) lookupLevel(level, key string) *levelBudget {
	switch level {
	case "org":
		return t.org
	case "team":
		return t.teams[key]
	case "user":
		return t.users[key]
	case "key":
		return t.keys[key]
	case "tag":
		return t.tags[key]
	}
	return nil
}

// maybeReset resets the counter if the current period has expired.
func (c *counter) maybeReset(period string, now time.Time) {
	start := PeriodStart(period, now)
	if start.After(c.periodStart) {
		c.totalSpend = 0
		c.modelSpend = make(map[string]float64)
		c.requests = 0
		c.periodStart = start
	}
}

// PeriodStart calculates the start of the current period.
func PeriodStart(period string, now time.Time) time.Time {
	switch period {
	case "daily":
		return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	case "weekly":
		weekday := now.Weekday()
		offset := int(weekday) - int(time.Monday)
		if offset < 0 {
			offset += 7
		}
		d := now.AddDate(0, 0, -offset)
		return time.Date(d.Year(), d.Month(), d.Day(), 0, 0, 0, 0, time.UTC)
	case "monthly":
		return time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	case "total":
		return time.Time{}
	default:
		return time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	}
}

func formatUSD(v float64) string {
	return strconv.FormatFloat(v, 'f', 2, 64)
}
