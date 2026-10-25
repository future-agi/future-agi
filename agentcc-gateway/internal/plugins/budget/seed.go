package budget

import (
	"log/slog"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// OrgSpendSummary holds spend data for a single org, used to seed
// per-org hierarchy counters on gateway startup.
type OrgSpendSummary struct {
	TotalSpend float64            `json:"total_spend"`
	PerKey     map[string]float64 `json:"per_key"`
	PerUser    map[string]float64 `json:"per_user"`
	PerModel   map[string]float64 `json:"per_model"`
}

// SeedOrgSpend initialises the per-org hierarchy counters from historical
// spend data retrieved from the control plane.
//
// cmd/agentcc/main.go reads the summary for the gateway's default period
// (budgets.default_period, else monthly: seedPeriod), so a counter is seeded
// only when it counts over that period; a counter of another period starts
// from the gateway's start. SeedOrgSpend seeds the org level of the org it
// is given, its budgets on, off or not set, as recordOrgHierarchicalSpend
// counts the org level for every org. A seed never lowers what the gateway has
// already counted in the same period.
func (p *Plugin) SeedOrgSpend(orgID string, summary OrgSpendSummary) {
	if p.tenantStore == nil {
		return
	}
	var budgets *tenant.BudgetsConfig
	if orgCfg := p.tenantStore.Get(orgID); orgCfg != nil {
		budgets = orgCfg.Budgets
	}

	hc := p.getOrgHierarchy(orgID)
	now := p.clock()
	seeded := budgetpkg.RefusalPeriod(p.seedPeriod())
	inSeedPeriod := func(period string) bool { return budgetpkg.RefusalPeriod(period) == seeded }

	// Seed org-level counter.
	if orgPeriod := orgBudgetPeriod(budgets); inSeedPeriod(orgPeriod) {
		seedCounter(hc.getOrCreateCounter("org", ""), orgPeriod, now, summary.TotalSpend, summary.PerModel)
	}
	if budgets == nil {
		slog.Debug("org spend seeded", "org_id", orgID, "total", summary.TotalSpend)
		return
	}

	defaultPeriod := budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}

	// Seed per-key counters.
	if budgets.Keys != nil {
		for keyName, lc := range budgets.Keys {
			if lc == nil || lc.Limit <= 0 {
				continue
			}
			spend, ok := summary.PerKey[keyName]
			if !ok {
				continue
			}
			period := lc.Period
			if period == "" {
				period = defaultPeriod
			}
			if !inSeedPeriod(period) {
				continue
			}
			bc := hc.getOrCreateCounter("key", keyName)
			seedCounter(bc, period, now, spend, nil)
		}
	}

	// Seed per-user counters.
	if budgets.Users != nil {
		for userName, lc := range budgets.Users {
			if lc == nil || lc.Limit <= 0 {
				continue
			}
			spend, ok := summary.PerUser[userName]
			if !ok {
				continue
			}
			period := lc.Period
			if period == "" {
				period = defaultPeriod
			}
			if !inSeedPeriod(period) {
				continue
			}
			bc := hc.getOrCreateCounter("user", userName)
			seedCounter(bc, period, now, spend, nil)
		}
	}

	if budgets.Enabled {
		slog.Info("org spend seeded", "org_id", orgID, "total", summary.TotalSpend)
	} else {
		slog.Debug("org spend seeded", "org_id", orgID, "total", summary.TotalSpend)
	}
}

// seedPeriod is the period cmd/agentcc/main.go reads the start-up spend
// summary for: the gateway's default budget period (the node tracker's,
// built from the same config), else monthly.
func (p *Plugin) seedPeriod() string {
	if p.tracker != nil {
		if period := p.tracker.DefaultPeriod(); period != "" {
			return period
		}
	}
	return "monthly"
}

// seedCounter sets a counter to the seed for the current period. A counter
// already in that period keeps the larger of what it counted and the seed,
// in total and per model: both count real spend, the seed from the request
// logs and the counter since the gateway started.
func seedCounter(bc *orgBudgetCounter, period string, now time.Time, totalSpend float64, modelSpend map[string]float64) {
	if bc == nil {
		return
	}
	bc.mu.Lock()
	defer bc.mu.Unlock()

	start := budgetpkg.PeriodStart(period, now)
	if !bc.periodStart.Equal(start) || bc.modelSpend == nil {
		bc.periodStart = start
		bc.totalSpend = 0
		bc.modelSpend = make(map[string]float64)
	}
	if totalSpend > bc.totalSpend {
		bc.totalSpend = totalSpend
	}
	for m, s := range modelSpend {
		if s > bc.modelSpend[m] {
			bc.modelSpend[m] = s
		}
	}
}
