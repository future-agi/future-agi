package budget

import (
	"log/slog"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
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
func (p *Plugin) SeedOrgSpend(orgID string, summary OrgSpendSummary) {
	if p.tenantStore == nil {
		return
	}
	orgCfg := p.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Budgets == nil || !orgCfg.Budgets.Enabled {
		return
	}

	hc := p.getOrgHierarchy(orgID)
	now := time.Now().UTC()

	defaultPeriod := orgCfg.Budgets.DefaultPeriod
	if defaultPeriod == "" {
		defaultPeriod = "monthly"
	}

	// Seed org-level counter.
	if orgCfg.Budgets.OrgLimit > 0 {
		orgPeriod := orgCfg.Budgets.OrgPeriod
		if orgPeriod == "" {
			orgPeriod = defaultPeriod
		}
		bc := hc.getOrCreateCounter("org", "")
		seedCounter(bc, orgPeriod, now, summary.TotalSpend, summary.PerModel)
	}

	// Seed per-key counters.
	if orgCfg.Budgets.Keys != nil {
		for keyName, lc := range orgCfg.Budgets.Keys {
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
			bc := hc.getOrCreateCounter("key", keyName)
			seedCounter(bc, period, now, spend, nil)
		}
	}

	// Seed per-user counters.
	if orgCfg.Budgets.Users != nil {
		for userName, lc := range orgCfg.Budgets.Users {
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
			bc := hc.getOrCreateCounter("user", userName)
			seedCounter(bc, period, now, spend, nil)
		}
	}

	slog.Info("org spend seeded", "org_id", orgID, "total", summary.TotalSpend)
}

func seedCounter(bc *orgBudgetCounter, period string, now time.Time, totalSpend float64, modelSpend map[string]float64) {
	if bc == nil {
		return
	}
	bc.mu.Lock()
	defer bc.mu.Unlock()

	start := budgetpkg.PeriodStart(period, now)
	bc.periodStart = start
	bc.totalSpend = totalSpend
	bc.modelSpend = make(map[string]float64)
	for m, s := range modelSpend {
		bc.modelSpend[m] = s
	}
}
