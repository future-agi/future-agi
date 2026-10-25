package budget

// The Budgets page's Set Budget save, as Django pushes it,
// enforced by the budget plugin. Each test first checks that Django's own
// functions push the fixture's budgets for the dialog's bodies (enabled, the
// threshold, the throttle and the later Hard Limit included),
// then decodes the fixture through the admin contract and runs the plugin.

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestBudgetEnabledFromConsole(t *testing.T) {
	t.Run("Set Budget turns budgets on: past the cap is refused", func(t *testing.T) {
		b := btDecode(t, btCase(t, "org_block"))
		if !b.Enabled {
			t.Fatalf("pushed budgets are off: %+v", b)
		}
		p, _ := btPlugin(t, b)
		if res, _ := btAsk(p, nil); res.Error != nil {
			t.Fatalf("first request refused before any spend: %s", res.Error.Code)
		}
		btSpend(t, p, "1.00", nil)
		res, _ := btAsk(p, nil)
		if btCode(res) != "org_budget_exceeded" || res.Error.Status != 429 {
			t.Fatalf("past the $0.000001 cap: got %s, want 429 org_budget_exceeded", btCode(res))
		}
	})
	t.Run("an explicit off stays off", func(t *testing.T) {
		b := btDecode(t, btCase(t, "explicit_off"))
		if b.Enabled {
			t.Fatalf("explicit enabled:false was pushed as on: %+v", b)
		}
		p, _ := btPlugin(t, b)
		btSpend(t, p, "1.00", nil)
		if res, _ := btAsk(p, nil); res.Error != nil {
			t.Fatalf("budgets switched off, yet refused: %s", res.Error.Code)
		}
	})
}

func TestBudgetThrottle(t *testing.T) {
	t.Run("console throttle: past the cap throttle_rpm a minute, then 429 with Retry-After", func(t *testing.T) {
		b := btDecode(t, btCase(t, "org_throttle"))
		p, clock := btPlugin(t, b)
		btSpend(t, p, "1.00", nil)
		var codes []string
		for i := 0; i < 3; i++ {
			res, _ := btAsk(p, nil)
			codes = append(codes, btCode(res))
		}
		if codes[0] != "allowed" || codes[1] != "allowed" || codes[2] != "budget_throttled" {
			t.Fatalf("throttle_rpm 2 past the cap: got %v, want [allowed allowed budget_throttled]", codes)
		}
		res, _ := btAsk(p, nil)
		if res.Error.Status != 429 || res.Error.RetryAfterSec != 50 {
			t.Fatalf("throttled: status %d Retry-After %d, want 429 and 50 (the minute ends at :00)", res.Error.Status, res.Error.RetryAfterSec)
		}
		clock.add(50 * time.Second)
		if res, _ := btAsk(p, nil); res.Error != nil {
			t.Fatalf("a new minute: refused %s", res.Error.Code)
		}
	})
	t.Run("under the cap a throttle level never throttles", func(t *testing.T) {
		b := btDecode(t, btCase(t, "org_throttle"))
		b.OrgLimit = 100
		p, _ := btPlugin(t, b)
		btSpend(t, p, "1.00", nil)
		for i := 0; i < 10; i++ {
			if res, _ := btAsk(p, nil); res.Error != nil {
				t.Fatalf("request %d under the cap refused: %s", i+1, res.Error.Code)
			}
		}
	})
	t.Run("throttle_rpm defaults to 1", func(t *testing.T) {
		p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{
			"eng": {Limit: 0.000001, Action: "throttle", Hard: boolPtr(false)},
		}})
		eng := map[string]string{"key_team": "eng"}
		btSpend(t, p, "1.00", eng)
		first, _ := btAsk(p, eng)
		second, _ := btAsk(p, eng)
		if btCode(first) != "allowed" || btCode(second) != "budget_throttled" {
			t.Fatalf("default throttle_rpm: got [%s %s], want [allowed budget_throttled]", btCode(first), btCode(second))
		}
	})
	t.Run("a later level's block does not use up an earlier level's throttle", func(t *testing.T) {
		p, _ := btPlugin(t, &tenant.BudgetsConfig{
			Enabled: true, OrgLimit: 0.000001, Action: "throttle", ThrottleRPM: 1,
			Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 0.000001}},
		})
		eng := map[string]string{"key_team": "eng"}
		btSpend(t, p, "1.00", eng)
		if res, _ := btAsk(p, eng); btCode(res) != "org_budget_exceeded" {
			t.Fatalf("team eng past its block cap: got %s", btCode(res))
		}
		if res, _ := btAsk(p, nil); res.Error != nil {
			t.Fatalf("the org's one throttle slot was spent by a refused request: %s", res.Error.Code)
		}
		if res, _ := btAsk(p, nil); btCode(res) != "budget_throttled" {
			t.Fatalf("second org request in the minute: got %s, want budget_throttled", btCode(res))
		}
	})
	t.Run("throttled answers come from the per-model cap too", func(t *testing.T) {
		p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, Keys: map[string]*tenant.BudgetLevelConfig{
			"ci": {Limit: 1000, Action: "throttle", ThrottleRPM: 1, PerModel: map[string]float64{"gpt-4o": 0.000001}},
		}})
		ci := map[string]string{"auth_key_name": "ci"}
		btSpend(t, p, "1.00", ci)
		first, _ := btAsk(p, ci)
		second, _ := btAsk(p, ci)
		if btCode(first) != "allowed" || btCode(second) != "budget_throttled" {
			t.Fatalf("per-model throttle: got [%s %s]", btCode(first), btCode(second))
		}
	})
}

func TestBudgetAlertThreshold(t *testing.T) {
	t.Run("the dialog's 50% moves the org warning", func(t *testing.T) {
		b := btDecode(t, btCase(t, "org_alert_50"))
		if b.WarnThreshold != 0.5 {
			t.Fatalf("warn_threshold %v, want 0.5", b.WarnThreshold)
		}
		at60, _ := btPlugin(t, b)
		btSpend(t, at60, "60.00", nil)
		if _, rc := btAsk(at60, nil); rc.Metadata["org_budget_warning"] != "true" {
			t.Fatalf("$60 of $100 at a 50%% threshold: no warning")
		}
		at40, _ := btPlugin(t, b)
		btSpend(t, at40, "40.00", nil)
		if _, rc := btAsk(at40, nil); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$40 of $100 at a 50%% threshold: warned")
		}
	})
	t.Run("a level's own alert threshold moves that level's warning", func(t *testing.T) {
		levels := map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 50, AlertThresholds: []float64{0.6}}}
		eng := map[string]string{"key_team": "eng"}
		over, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: levels})
		btSpend(t, over, "31.00", eng)
		if _, rc := btAsk(over, eng); rc.Metadata["org_budget_warning"] != "true" {
			t.Fatalf("$31 of $50 at a 60%% level threshold: no warning")
		}
		under, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: levels})
		btSpend(t, under, "29.00", eng)
		if _, rc := btAsk(under, eng); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$29 of $50 at a 60%% level threshold: warned")
		}
	})
	t.Run("a level without its own thresholds keeps the org default", func(t *testing.T) {
		eng := map[string]string{"key_team": "eng"}
		p, _ := btPlugin(t, &tenant.BudgetsConfig{Enabled: true, WarnThreshold: 0.9,
			Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 100}}})
		btSpend(t, p, "85.00", eng)
		if _, rc := btAsk(p, eng); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$85 of $100 under the org's 90%% default: warned")
		}
	})
}

func TestBudgetHardLimitLatest(t *testing.T) {
	t.Run("a later Hard Limit save wins over an older Organization budget", func(t *testing.T) {
		b := btDecode(t, btCase(t, "hard_limit_later"))
		p, _ := btPlugin(t, b)
		btSpend(t, p, "1.00", nil)
		if res, _ := btAsk(p, nil); btCode(res) != "org_budget_exceeded" {
			t.Fatalf("Hard Limit $0.000001 saved last: got %s, want org_budget_exceeded", btCode(res))
		}
	})
	t.Run("a later Organization save wins over an older Hard Limit", func(t *testing.T) {
		b := btDecode(t, btCase(t, "org_limit_later"))
		p, _ := btPlugin(t, b)
		btSpend(t, p, "1.00", nil)
		if res, _ := btAsk(p, nil); res.Error != nil {
			t.Fatalf("Organization $100 saved last: refused %s", res.Error.Code)
		}
	})
}
