package budget

// R7-BUDGET: a budget set partway through a period is checked
// against the period's spend so far, as the Budgets page's bar shows it. The
// gateway counts every organization's spend at the org level whether or not
// its budgets are on or set, so Set Budget (which turns budgets on) enforces
// against what the organization already spent; the start-up seed counts an
// organization whose budgets are off too.

import (
	"context"
	"testing"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// r7MidPlugin is the budget plugin over r7Org's config (nil: the org has no
// config yet) with the test clock, and the store it reads.
func r7MidPlugin(cfg *tenant.OrgConfig) (*Plugin, *tenant.Store) {
	store := tenant.NewStore()
	if cfg != nil {
		store.Set(r7Org, cfg)
	}
	p := New(nil, testPricing(), false, store)
	clock := &r7Clock{t: r7Now}
	p.now = clock.now
	return p, store
}

// r7SetBudgets saves the org's budgets the way a console push lands:
// PUT /-/orgs/{org}/config sets the config, then main.go's
// onOrgConfigChange calls InvalidateOrg.
func r7SetBudgets(p *Plugin, store *tenant.Store, b *tenant.BudgetsConfig) {
	store.Set(r7Org, &tenant.OrgConfig{Budgets: b})
	p.InvalidateOrg(r7Org)
}

// r7OrgBlock is Set Budget's Organization budget with Block, as pushed.
func r7OrgBlock(limit float64) *tenant.BudgetsConfig {
	return &tenant.BudgetsConfig{Enabled: true, OrgLimit: limit, HardLimit: true, Action: tenant.BudgetActionBlock}
}

func TestR7BudgetMidPeriod(t *testing.T) {
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
			p, store := r7MidPlugin(tc.cfg)
			for i := 0; i < 5; i++ {
				r7Spend(t, p, "1.00", nil)
			}
			r7SetBudgets(p, store, r7OrgBlock(1))
			res, rc := r7Ask(p, nil)
			if code := r7Code(res); code != "org_budget_exceeded" {
				t.Fatalf("first request after Set Budget: %s (org_budget_remaining %q), want org_budget_exceeded: the $5 spent before budgets were on was not counted",
					code, rc.Metadata["org_budget_remaining"])
			}
			if level := rc.Metadata["budget_exceeded_level"]; level != "org" {
				t.Fatalf("budget_exceeded_level %q, want org", level)
			}
		})
	}

	t.Run("a limit above the spend so far admits with what is left", func(t *testing.T) {
		p, store := r7MidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			r7Spend(t, p, "1.00", nil)
		}
		r7SetBudgets(p, store, r7OrgBlock(8))
		res, rc := r7Ask(p, nil)
		if code := r7Code(res); code != "allowed" {
			t.Fatalf("$5 of $8: %s", code)
		}
		if got := rc.Metadata["org_budget_remaining"]; got != "3.00" {
			t.Fatalf("org_budget_remaining %q, want 3.00", got)
		}
	})

	t.Run("an org without budgets gets no budget metadata", func(t *testing.T) {
		p, _ := r7MidPlugin(&tenant.OrgConfig{})
		rc := r7RC(map[string]string{"cost": "1.00"})
		rc.Response = &models.ChatCompletionResponse{}
		p.ProcessResponse(context.Background(), rc)
		for _, k := range []string{"org_budget_remaining", "org_budget_warning", "org_budget_blocked"} {
			if v, ok := rc.Metadata[k]; ok {
				t.Fatalf("response of an org without budgets carries %s=%q", k, v)
			}
		}
		res, ask := r7Ask(p, nil)
		if r7Code(res) != "allowed" {
			t.Fatalf("org without budgets refused: %s", r7Code(res))
		}
		if v, ok := ask.Metadata["org_budget_remaining"]; ok {
			t.Fatalf("request of an org without budgets carries org_budget_remaining=%q", v)
		}
	})

	t.Run("the spend so far is written through to Redis and read back", func(t *testing.T) {
		fake := &r7FakeRedis{}
		p, store := r7MidPlugin(&tenant.OrgConfig{})
		p.SetRedisBudget(fake)
		for i := 0; i < 5; i++ {
			r7Spend(t, p, "1.00", nil)
		}
		if got := fake.spend[r7Org+"|org||monthly"]; got != 5 {
			t.Fatalf("Redis org spend %v, want 5", got)
		}
		r7SetBudgets(p, store, r7OrgBlock(1))
		if res, _ := r7Ask(p, nil); r7Code(res) != "org_budget_exceeded" {
			t.Fatalf("with Redis: %s, want org_budget_exceeded", r7Code(res))
		}
	})

	t.Run("a budget level switched off keeps counting, so switching it on enforces", func(t *testing.T) {
		off := &tenant.BudgetsConfig{Enabled: false, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 2}}}
		p, store := r7MidPlugin(&tenant.OrgConfig{Budgets: off})
		team := map[string]string{"key_team": "eng"}
		for i := 0; i < 3; i++ {
			r7Spend(t, p, "1.00", team)
		}
		r7SetBudgets(p, store, &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 2}}})
		res, rc := r7Ask(p, team)
		if r7Code(res) != "org_budget_exceeded" || rc.Metadata["budget_exceeded_level"] != "team:eng" {
			t.Fatalf("team eng after $3 of $2: %s %q", r7Code(res), rc.Metadata["budget_exceeded_level"])
		}
	})

	t.Run("removing the org's config keeps the period's spend", func(t *testing.T) {
		p, store := r7MidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			r7Spend(t, p, "1.00", nil)
		}
		store.Delete(r7Org)
		p.InvalidateOrg(r7Org)
		r7SetBudgets(p, store, r7OrgBlock(1))
		if res, _ := r7Ask(p, nil); r7Code(res) != "org_budget_exceeded" {
			t.Fatalf("after the config was removed and set again: %s", r7Code(res))
		}
	})

	// Known limit (DOCS-FRAGMENT): the gateway counts the org level in the
	// org's current period; a budget whose period changes counts that period
	// from the change.
	t.Run("a change of period counts the new period from the change", func(t *testing.T) {
		p, store := r7MidPlugin(&tenant.OrgConfig{})
		for i := 0; i < 5; i++ {
			r7Spend(t, p, "1.00", nil)
		}
		daily := r7OrgBlock(1)
		daily.OrgPeriod = "daily"
		r7SetBudgets(p, store, daily)
		res, rc := r7Ask(p, nil)
		if r7Code(res) != "allowed" || rc.Metadata["org_budget_remaining"] != "1.00" {
			t.Fatalf("daily budget after monthly spend: %s remaining %q, want allowed 1.00", r7Code(res), rc.Metadata["org_budget_remaining"])
		}
	})
}

func TestR7BudgetSeedMidPeriod(t *testing.T) {
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
		t.Run("the start-up seed counts the org level: "+tc.name, func(t *testing.T) {
			p, store := r7MidPlugin(tc.cfg)
			p.tracker = monthly()
			p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 5})
			r7SetBudgets(p, store, r7OrgBlock(1))
			if res, _ := r7Ask(p, nil); r7Code(res) != "org_budget_exceeded" {
				t.Fatalf("$5 seeded, then Set Budget $1 Block: %s", r7Code(res))
			}
		})
	}

	t.Run("the seed is the gateway's default period's: a daily org budget is not seeded with this month's spend", func(t *testing.T) {
		daily := r7OrgBlock(10)
		daily.OrgPeriod = "daily"
		p, _ := r7MidPlugin(&tenant.OrgConfig{Budgets: daily})
		p.tracker = monthly()
		p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 50})
		res, rc := r7Ask(p, nil)
		if r7Code(res) != "allowed" || rc.Metadata["org_budget_remaining"] != "10.00" {
			t.Fatalf("daily $10 budget after a $50 monthly seed: %s remaining %q, want allowed 10.00", r7Code(res), rc.Metadata["org_budget_remaining"])
		}
	})

	t.Run("a gateway whose default period is daily seeds a daily org budget", func(t *testing.T) {
		daily := r7OrgBlock(10)
		daily.OrgPeriod = "daily"
		p, _ := r7MidPlugin(&tenant.OrgConfig{Budgets: daily})
		p.tracker = budgetpkg.NewTracker(config.BudgetsConfig{Enabled: true, DefaultPeriod: "daily"})
		p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 4})
		_, rc := r7Ask(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "6.00" {
			t.Fatalf("org_budget_remaining %q, want 6.00", got)
		}
	})

	t.Run("the seed never lowers what the gateway already counted this period", func(t *testing.T) {
		p, _ := r7MidPlugin(&tenant.OrgConfig{Budgets: r7OrgBlock(10)})
		p.tracker = monthly()
		for i := 0; i < 6; i++ {
			r7Spend(t, p, "1.00", nil)
		}
		p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 2})
		_, rc := r7Ask(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "4.00" {
			t.Fatalf("org_budget_remaining %q, want 4.00 ($6 counted, $2 seeded)", got)
		}
	})

	t.Run("a larger seed raises the count", func(t *testing.T) {
		p, _ := r7MidPlugin(&tenant.OrgConfig{Budgets: r7OrgBlock(10)})
		p.tracker = monthly()
		r7Spend(t, p, "1.00", nil)
		p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 7})
		_, rc := r7Ask(p, nil)
		if got := rc.Metadata["org_budget_remaining"]; got != "3.00" {
			t.Fatalf("org_budget_remaining %q, want 3.00", got)
		}
	})
}

// BenchmarkR7BudgetResponseNoBudgets is ProcessResponse for an org with no
// budgets, which now counts its org level (one in-memory counter, no
// response metadata): the cost every org without budgets now pays.
func BenchmarkR7BudgetResponseNoBudgets(b *testing.B) {
	p, _ := r7MidPlugin(&tenant.OrgConfig{})
	rc := r7RC(map[string]string{"cost": "0.01"})
	rc.Response = &models.ChatCompletionResponse{}
	ctx := context.Background()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		p.ProcessResponse(ctx, rc)
	}
}
