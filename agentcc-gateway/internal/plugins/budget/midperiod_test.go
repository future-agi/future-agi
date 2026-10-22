package budget

// A budget set partway through a period is checked
// against the period's spend so far, as the Budgets page's bar shows it. The
// gateway counts every organization's spend at the org level whether or not
// its budgets are on or set, so Set Budget (which turns budgets on) enforces
// against what the organization already spent; SeedOrgSpend seeds the org
// level of an organization whose budgets are off too.

import (
	"context"
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// btMidPlugin is the budget plugin over btOrg's config (nil: the org has no
// config yet) with the test clock, and the store it reads.
func btMidPlugin(cfg *tenant.OrgConfig) (*Plugin, *tenant.Store) {
	store := tenant.NewStore()
	if cfg != nil {
		store.Set(btOrg, cfg)
	}
	p := New(nil, testPricing(), false, store)
	clock := &btClock{t: btNow}
	p.now = clock.now
	return p, store
}

// btSetBudgets saves the org's budgets the way a console push lands:
// PUT /-/orgs/{org}/config sets the config, then main.go's
// onOrgConfigChange calls InvalidateOrg.
func btSetBudgets(p *Plugin, store *tenant.Store, b *tenant.BudgetsConfig) {
	store.Set(btOrg, &tenant.OrgConfig{Budgets: b})
	p.InvalidateOrg(btOrg)
}

// btOrgBlock is Set Budget's Organization budget with Block, as pushed.
func btOrgBlock(limit float64) *tenant.BudgetsConfig {
	return &tenant.BudgetsConfig{Enabled: true, OrgLimit: limit, HardLimit: true, Action: tenant.BudgetActionBlock}
}

func TestBudgetMidPeriod(t *testing.T) {
	before := []struct {
		name string
		cfg  *tenant.OrgConfig
	}{
		{"no budgets section", &tenant.OrgConfig{}},
		{"an empty budgets section", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{}}},
		{"budgets switched off", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: false, OrgLimit: 1, HardLimit: true}}},
		{"budgets on with only a team budget", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{
			Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 100}},
		}}},
		{"no org config yet", nil},
	}
	for _, tc := range before {
		t.Run("$5 spent before Set Budget $1 Block is refused: "+tc.name, func(t *testing.T) {
			p, store := btMidPlugin(tc.cfg)
			for i := 0; i < 5; i++ {
				btSpend(t, p, "1.00", nil)
			}
			btSetBudgets(p, store, btOrgBlock(1))
			res, rc := btAsk(p, nil)
			if code := btCode(res); code != "org_budget_exceeded" {
				t.Fatalf("first request after Set Budget: %s (org_budget_remaining %q), want org_budget_exceeded: the $5 spent before budgets were on was not counted",
					code, rc.Metadata["org_budget_remaining"])
			}
			if level := rc.Metadata["budget_exceeded_level"]; level != "org" {
				t.Fatalf("budget_exceeded_level %q, want org", level)
			}
		})
	}

	t.Run("a limit above the spend so far admits with what is left", func(t *testing.T) {
		p, store := btMidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			btSpend(t, p, "1.00", nil)
		}
		btSetBudgets(p, store, btOrgBlock(8))
		res, rc := btAsk(p, nil)
		if code := btCode(res); code != "allowed" {
			t.Fatalf("$5 of $8: %s", code)
		}
		if got := rc.Metadata["org_budget_remaining"]; got != "3.00" {
			t.Fatalf("org_budget_remaining %q, want 3.00", got)
		}
	})

	t.Run("an org without budgets gets no budget metadata", func(t *testing.T) {
		p, _ := btMidPlugin(&tenant.OrgConfig{})
		rc := btRC(map[string]string{"cost": "1.00"})
		rc.Response = &models.ChatCompletionResponse{}
		p.ProcessResponse(context.Background(), rc)
		for _, k := range []string{"org_budget_remaining", "org_budget_warning", "org_budget_blocked"} {
			if v, ok := rc.Metadata[k]; ok {
				t.Fatalf("response of an org without budgets carries %s=%q", k, v)
			}
		}
		res, ask := btAsk(p, nil)
		if btCode(res) != "allowed" {
			t.Fatalf("org without budgets refused: %s", btCode(res))
		}
		if v, ok := ask.Metadata["org_budget_remaining"]; ok {
			t.Fatalf("request of an org without budgets carries org_budget_remaining=%q", v)
		}
	})

	t.Run("the spend so far is written through to Redis and read back", func(t *testing.T) {
		fake := &btFakeRedis{}
		p, store := btMidPlugin(&tenant.OrgConfig{})
		p.SetRedisBudget(fake)
		for i := 0; i < 5; i++ {
			btSpend(t, p, "1.00", nil)
		}
		if got := fake.spend[btOrg+"|org||monthly"]; got != 5 {
			t.Fatalf("Redis org spend %v, want 5", got)
		}
		btSetBudgets(p, store, btOrgBlock(1))
		if res, _ := btAsk(p, nil); btCode(res) != "org_budget_exceeded" {
			t.Fatalf("with Redis: %s, want org_budget_exceeded", btCode(res))
		}
	})

	t.Run("a budget level switched off keeps counting, so switching it on enforces", func(t *testing.T) {
		off := &tenant.BudgetsConfig{Enabled: false, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 2}}}
		p, store := btMidPlugin(&tenant.OrgConfig{Budgets: off})
		team := map[string]string{"key_team": "eng"}
		for i := 0; i < 3; i++ {
			btSpend(t, p, "1.00", team)
		}
		btSetBudgets(p, store, &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 2}}})
		res, rc := btAsk(p, team)
		if btCode(res) != "org_budget_exceeded" || rc.Metadata["budget_exceeded_level"] != "team:eng" {
			t.Fatalf("team eng after $3 of $2: %s %q", btCode(res), rc.Metadata["budget_exceeded_level"])
		}
	})

	t.Run("removing the org's config keeps the period's spend", func(t *testing.T) {
		p, store := btMidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			btSpend(t, p, "1.00", nil)
		}
		store.Delete(btOrg)
		p.InvalidateOrg(btOrg)
		btSetBudgets(p, store, btOrgBlock(1))
		if res, _ := btAsk(p, nil); btCode(res) != "org_budget_exceeded" {
			t.Fatalf("after the config was removed and set again: %s", btCode(res))
		}
	})

	// A change of period counts the new period from the change.
	t.Run("a change of period counts the new period from the change", func(t *testing.T) {
		p, store := btMidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			btSpend(t, p, "1.00", nil)
		}
		daily := btOrgBlock(1)
		daily.OrgPeriod = "daily"
		btSetBudgets(p, store, daily)
		res, rc := btAsk(p, nil)
		if btCode(res) != "allowed" || rc.Metadata["org_budget_remaining"] != "1.00" {
			t.Fatalf("daily budget after monthly spend: %s remaining %q, want allowed 1.00", btCode(res), rc.Metadata["org_budget_remaining"])
		}
	})
}

func TestBudgetSeedMidPeriod(t *testing.T) {
	monthly := func() *budgetpkg.Tracker { return budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true}) }

	seeded := []struct {
		name string
		cfg  *tenant.OrgConfig
	}{
		{"budgets switched off", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: false}}},
		{"no budgets section", &tenant.OrgConfig{}},
		{"budgets on with only a team budget", &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{
			Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 100}},
		}}},
	}
	for _, tc := range seeded {
		t.Run("SeedOrgSpend seeds the org level: "+tc.name, func(t *testing.T) {
			p, store := btMidPlugin(tc.cfg)
			p.tracker = monthly()
			p.SeedOrgSpend(btOrg, OrgSpendSummary{TotalSpend: 5})
			btSetBudgets(p, store, btOrgBlock(1))
			if res, _ := btAsk(p, nil); btCode(res) != "org_budget_exceeded" {
				t.Fatalf("$5 seeded, then Set Budget $1 Block: %s", btCode(res))
			}
		})
	}

	t.Run("the seed is the gateway's default period's: a daily org budget is not seeded with this month's spend", func(t *testing.T) {
		daily := btOrgBlock(10)
		daily.OrgPeriod = "daily"
		p, _ := btMidPlugin(&tenant.OrgConfig{Budgets: daily})
		p.tracker = monthly()
		p.SeedOrgSpend(btOrg, OrgSpendSummary{TotalSpend: 50})
		res, rc := btAsk(p, nil)
		if btCode(res) != "allowed" || rc.Metadata["org_budget_remaining"] != "10.00" {
			t.Fatalf("daily $10 budget after a $50 monthly seed: %s remaining %q, want allowed 10.00", btCode(res), rc.Metadata["org_budget_remaining"])
		}
	})

	t.Run("a gateway whose default period is daily seeds a daily org budget", func(t *testing.T) {
		daily := btOrgBlock(10)
		daily.OrgPeriod = "daily"
		p, _ := btMidPlugin(&tenant.OrgConfig{Budgets: daily})
		p.tracker = budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "daily"})
		p.SeedOrgSpend(btOrg, OrgSpendSummary{TotalSpend: 4})
		_, rc := btAsk(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "6.00" {
			t.Fatalf("org_budget_remaining %q, want 6.00", got)
		}
	})

	t.Run("the seed never lowers what the gateway already counted this period", func(t *testing.T) {
		p, _ := btMidPlugin(&tenant.OrgConfig{Budgets: btOrgBlock(10)})
		p.tracker = monthly()
		for i := 0; i < 6; i++ {
			btSpend(t, p, "1.00", nil)
		}
		p.SeedOrgSpend(btOrg, OrgSpendSummary{TotalSpend: 2})
		_, rc := btAsk(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "4.00" {
			t.Fatalf("org_budget_remaining %q, want 4.00 ($6 counted, $2 seeded)", got)
		}
	})

	t.Run("a larger seed raises the count", func(t *testing.T) {
		p, _ := btMidPlugin(&tenant.OrgConfig{Budgets: btOrgBlock(10)})
		p.tracker = monthly()
		btSpend(t, p, "1.00", nil)
		p.SeedOrgSpend(btOrg, OrgSpendSummary{TotalSpend: 7})
		_, rc := btAsk(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "3.00" {
			t.Fatalf("org_budget_remaining %q, want 3.00", got)
		}
	})
}

// BenchmarkBudgetResponseNoBudgets is ProcessResponse for an org with no
// budgets, which counts its org level (one in-memory counter, no
// response metadata): the cost every org without budgets pays.
func BenchmarkBudgetResponseNoBudgets(b *testing.B) {
	p, _ := btMidPlugin(&tenant.OrgConfig{})
	rc := btRC(map[string]string{"cost": "0.01"})
	rc.Response = &models.ChatCompletionResponse{}
	ctx := context.Background()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		p.ProcessResponse(ctx, rc)
	}
}
