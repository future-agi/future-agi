package budget

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A budget refusal names the configured amount as it was set: a limit below a
// cent reads $0.0003, not $0.00. The code and the status are unchanged.
func TestBudgetRefusalShowsSubCentAmounts(t *testing.T) {
	team := map[string]string{"key_team": "eng"}
	cases := []struct {
		name    string
		budgets *tenant.BudgetsConfig
		meta    map[string]string
		spend   string
		want    string
	}{
		{
			name:    "an org limit below a cent",
			budgets: r7OrgBlock(0.0003),
			spend:   "0.0004",
			want:    "org budget exceeded: spent $0.0004 of $0.0003 ",
		},
		{
			name: "a team limit below a cent, with four significant digits of spend",
			budgets: &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{
				"eng": {Limit: 0.005},
			}},
			meta:  team,
			spend: "0.0071234",
			want:  "team 'eng' budget exceeded: spent $0.007123 of $0.005 ",
		},
		{
			name: "a per-model limit below a cent",
			budgets: &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{
				"eng": {Limit: 1000, PerModel: map[string]float64{"gpt-4o": 0.0003}},
			}},
			meta:  team,
			spend: "0.0005",
			want:  "per-model budget exceeded: model 'gpt-4o' spent $0.0005 of $0.0003 ",
		},
		{
			name:    "amounts of a cent or more keep two decimals",
			budgets: r7OrgBlock(1),
			spend:   "1.25",
			want:    "org budget exceeded: spent $1.25 of $1.00 ",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p, store := r7MidPlugin(&tenant.OrgConfig{})
			r7SetBudgets(p, store, tc.budgets)
			r7Spend(t, p, tc.spend, tc.meta)
			res, _ := r7Ask(p, tc.meta)
			if res.Error == nil {
				t.Fatalf("allowed, want org_budget_exceeded")
			}
			if res.Error.Code != "org_budget_exceeded" || res.Error.Status != http.StatusTooManyRequests {
				t.Fatalf("code %q status %d, want org_budget_exceeded 429", res.Error.Code, res.Error.Status)
			}
			if !strings.Contains(res.Error.Message, tc.want) {
				t.Fatalf("message %q, want it to contain %q", res.Error.Message, tc.want)
			}
		})
	}
}

func TestBudgetAmountFormatting(t *testing.T) {
	for _, tc := range []struct {
		in   float64
		want string
	}{
		{0, "0.00"},
		{1, "1.00"},
		{1.25, "1.25"},
		{0.01, "0.01"},
		{0.009999, "0.009999"},
		{0.005, "0.005"},
		{0.0003, "0.0003"},
		{0.00031234, "0.0003123"},
		{0.000001, "0.000001"},
	} {
		if got := usdAmount(tc.in); got != tc.want {
			t.Errorf("usdAmount(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
