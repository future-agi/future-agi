package budget

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// platformFunded is the request metadata of a key the platform pays for: the
// auth plugin writes key_billing from the key's platform-set billing.
func platformFunded(name string) map[string]string {
	return map[string]string{"key_billing": "platform_funded", "auth_key_name": name, "auth_key_owner": "owner-run"}
}

func orgCounterSpend(p *Plugin, level, key string) float64 {
	bc := p.getOrgHierarchy(r7Org).getOrCreateCounter(level, key)
	total, _ := p.getCounterSpend(bc, "monthly", r7Now)
	return total
}

// A platform-funded key's spend is logged elsewhere with its cost, and is not
// counted in the org, key-name or user counters.
func TestPlatformFundedSpendIsNotCountedAgainstTheOrg(t *testing.T) {
	p, store := r7MidPlugin(&tenant.OrgConfig{})
	r7SetBudgets(p, store, &tenant.BudgetsConfig{Enabled: true, OrgLimit: 100, Action: tenant.BudgetActionBlock,
		Keys:  map[string]*tenant.BudgetLevelConfig{"run": {Limit: 100}},
		Users: map[string]*tenant.BudgetLevelConfig{"owner-run": {Limit: 100}}})
	for i := 0; i < 5; i++ {
		r7Spend(t, p, "1.00", platformFunded("run"))
	}
	for _, c := range []struct{ level, key string }{{"org", ""}, {"key", "run"}, {"user", "owner-run"}} {
		if got := orgCounterSpend(p, c.level, c.key); got != 0 {
			t.Errorf("%s %q counter = %.2f after platform-funded calls, want 0", c.level, c.key, got)
		}
	}
	// An ordinary key's spend is counted as today.
	r7Spend(t, p, "1.00", map[string]string{"auth_key_name": "run"})
	if got := orgCounterSpend(p, "org", ""); got != 1 {
		t.Errorf("org counter = %.2f after an ordinary $1 call, want 1", got)
	}
}

// The org's gateway budget checks do not refuse a platform-funded call; an
// ordinary key of the same org is refused as today.
func TestOrgBudgetDoesNotRefuseAPlatformFundedCall(t *testing.T) {
	p, store := r7MidPlugin(&tenant.OrgConfig{})
	for i := 0; i < 5; i++ {
		r7Spend(t, p, "1.00", nil)
	}
	r7SetBudgets(p, store, r7OrgBlock(1))
	if res, _ := r7Ask(p, nil); r7Code(res) != "org_budget_exceeded" {
		t.Fatalf("an ordinary call over the org budget: %s, want org_budget_exceeded", r7Code(res))
	}
	if res, rc := r7Ask(p, platformFunded("run")); res.Error != nil {
		t.Fatalf("a platform-funded call was refused by the org budget: %s (%s)", r7Code(res), rc.Metadata["budget_exceeded_level"])
	}
}

// The start-up seed reads Django's spend summary, which leaves out
// platform-funded rows: the seeded org spend equals the summary, and later
// platform-funded calls add nothing to it.
func TestSeededOrgSpendEqualsTheSummary(t *testing.T) {
	p, _ := r7MidPlugin(&tenant.OrgConfig{})
	p.SeedOrgSpend(r7Org, OrgSpendSummary{TotalSpend: 2.5})
	r7Spend(t, p, "1.00", platformFunded("run"))
	if got := orgCounterSpend(p, "org", ""); got != 2.5 {
		t.Fatalf("org counter = %.2f, want the summary's 2.50", got)
	}
}

// An org's strict daily caps neither refuse nor count a platform-funded call:
// with the cap used up, an ordinary key of the org is refused and a
// platform-funded call is served, holds no cap state, and its cost never
// reaches the cap store.
func TestStrictCapsDoNotRefuseOrCountAPlatformFundedCall(t *testing.T) {
	ctx := context.Background()
	p := capMoneyPlugin(t)
	spend := func(rc *models.RequestContext) {
		rc.Response = &models.ChatCompletionResponse{Usage: models.MeasuredUsage(1, 0)}
		rc.Billing.UpstreamMicros, rc.Billing.BilledMicros = 3000, 3150
	}

	// Use up org-a's agent cap with an ordinary key.
	first := capMoneyRequest(t, "org-a", "key-a", true)
	if res := p.ProcessRequest(ctx, first); res.Error != nil {
		t.Fatal(res.Error)
	}
	if !RequestAdmission(first).ClaimAttempt() {
		t.Fatal("attempt refused")
	}
	spend(first)
	p.ProcessResponse(ctx, first)

	normal := capMoneyRequest(t, "org-a", "key-b", true)
	if res := p.ProcessRequest(ctx, normal); res.Error == nil || res.Error.Code != "org_budget_exceeded" {
		t.Fatalf("an ordinary key over the org's used-up cap: %v, want org_budget_exceeded", res.Error)
	}

	funded := capMoneyRequest(t, "org-a", "key-b", true)
	funded.Metadata["key_billing"] = "platform_funded"
	if res := p.ProcessRequest(ctx, funded); res.Error != nil {
		t.Fatalf("a platform-funded call was refused by the org's cap: %v", res.Error)
	}
	if _, held := p.capStates.Load(funded); held {
		t.Fatal("a platform-funded call holds cap state")
	}
	spend(funded)
	p.ProcessResponse(ctx, funded)

	// Platform-funded spend on a fresh org leaves its cap untouched: two
	// platform-funded calls cost more than the cap, and an ordinary call is
	// still admitted after them.
	for i := 0; i < 2; i++ {
		rc := capMoneyRequest(t, "org-c", "key-b", true)
		rc.Metadata["key_billing"] = "platform_funded"
		if res := p.ProcessRequest(ctx, rc); res.Error != nil {
			t.Fatalf("platform-funded call %d refused: %v", i, res.Error)
		}
		spend(rc)
		p.ProcessResponse(ctx, rc)
	}
	if e := p.reserveCaps(ctx, capMoneyRequest(t, "org-c", "key-a", true)); e != nil {
		t.Fatalf("platform-funded spend reached org-c's cap: %v", e)
	}
}
