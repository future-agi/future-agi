package budget

// The Budgets page's Set Budget save, as Django pushes it,
// enforced by the budget plugin. Each test first checks that Django's own
// functions push the fixture's budgets for the dialog's bodies (red while the
// push left out enabled, the threshold, the throttle or the later Hard Limit),
// then decodes the fixture through the admin contract and runs the plugin.

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestR7BudgetEnabledFromConsole(t *testing.T) {
	t.Run("Set Budget turns budgets on: past the cap is refused", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "org_block"))
		if !b.Enabled {
			t.Fatalf("pushed budgets are off: %+v", b)
		}
		p, _ := r7Plugin(t, b)
		if res, _ := r7Ask(p, nil); res.Error != nil {
			t.Fatalf("first request refused before any spend: %s", res.Error.Code)
		}
		r7Spend(t, p, "1.00", nil)
		res, _ := r7Ask(p, nil)
		if r7Code(res) != "org_budget_exceeded" || res.Error.Status != 429 {
			t.Fatalf("past the $0.000001 cap: got %s, want 429 org_budget_exceeded", r7Code(res))
		}
	})
	t.Run("an explicit off stays off", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "explicit_off"))
		if b.Enabled {
			t.Fatalf("explicit enabled:false was pushed as on: %+v", b)
		}
		p, _ := r7Plugin(t, b)
		r7Spend(t, p, "1.00", nil)
		if res, _ := r7Ask(p, nil); res.Error != nil {
			t.Fatalf("budgets switched off, yet refused: %s", res.Error.Code)
		}
	})
}

func TestR7BudgetThrottle(t *testing.T) {
	t.Run("console throttle: past the cap throttle_rpm a minute, then 429 with Retry-After", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "org_throttle"))
		p, clock := r7Plugin(t, b)
		r7Spend(t, p, "1.00", nil)
		var codes []string
		for i := 0; i < 3; i++ {
			res, _ := r7Ask(p, nil)
			codes = append(codes, r7Code(res))
		}
		if codes[0] != "allowed" || codes[1] != "allowed" || codes[2] != "budget_throttled" {
			t.Fatalf("throttle_rpm 2 past the cap: got %v, want [allowed allowed budget_throttled]", codes)
		}
		res, _ := r7Ask(p, nil)
		if res.Error.Status != 429 || res.Error.RetryAfterSec != 50 {
			t.Fatalf("throttled: status %d Retry-After %d, want 429 and 50 (the minute ends at :00)", res.Error.Status, res.Error.RetryAfterSec)
		}
		clock.add(50 * time.Second)
		if res, _ := r7Ask(p, nil); res.Error != nil {
			t.Fatalf("a new minute: refused %s", res.Error.Code)
		}
	})
	t.Run("under the cap a throttle level never throttles", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "org_throttle"))
		b.OrgLimit = 100
		p, _ := r7Plugin(t, b)
		r7Spend(t, p, "1.00", nil)
		for i := 0; i < 10; i++ {
			if res, _ := r7Ask(p, nil); res.Error != nil {
				t.Fatalf("request %d under the cap refused: %s", i+1, res.Error.Code)
			}
		}
	})
	t.Run("throttle_rpm defaults to 1", func(t *testing.T) {
		p, _ := r7Plugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: map[string]*tenant.BudgetLevelConfig{
			"eng": {Limit: 0.000001, Action: "throttle", Hard: boolPtr(false)},
		}})
		eng := map[string]string{"key_team": "eng"}
		r7Spend(t, p, "1.00", eng)
		first, _ := r7Ask(p, eng)
		second, _ := r7Ask(p, eng)
		if r7Code(first) != "allowed" || r7Code(second) != "budget_throttled" {
			t.Fatalf("default throttle_rpm: got [%s %s], want [allowed budget_throttled]", r7Code(first), r7Code(second))
		}
	})
	t.Run("a later level's block does not use up an earlier level's throttle", func(t *testing.T) {
		p, _ := r7Plugin(t, &tenant.BudgetsConfig{
			Enabled: true, OrgLimit: 0.000001, Action: "throttle", ThrottleRPM: 1,
			Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 0.000001}},
		})
		eng := map[string]string{"key_team": "eng"}
		r7Spend(t, p, "1.00", eng)
		if res, _ := r7Ask(p, eng); r7Code(res) != "org_budget_exceeded" {
			t.Fatalf("team eng past its block cap: got %s", r7Code(res))
		}
		if res, _ := r7Ask(p, nil); res.Error != nil {
			t.Fatalf("the org's one throttle slot was spent by a refused request: %s", res.Error.Code)
		}
		if res, _ := r7Ask(p, nil); r7Code(res) != "budget_throttled" {
			t.Fatalf("second org request in the minute: got %s, want budget_throttled", r7Code(res))
		}
	})
	t.Run("throttled answers come from the per-model cap too", func(t *testing.T) {
		p, _ := r7Plugin(t, &tenant.BudgetsConfig{Enabled: true, Keys: map[string]*tenant.BudgetLevelConfig{
			"ci": {Limit: 1000, Action: "throttle", ThrottleRPM: 1, PerModel: map[string]float64{"gpt-4o": 0.000001}},
		}})
		ci := map[string]string{"auth_key_name": "ci"}
		r7Spend(t, p, "1.00", ci)
		first, _ := r7Ask(p, ci)
		second, _ := r7Ask(p, ci)
		if r7Code(first) != "allowed" || r7Code(second) != "budget_throttled" {
			t.Fatalf("per-model throttle: got [%s %s]", r7Code(first), r7Code(second))
		}
	})
}

func TestR7BudgetAlertThreshold(t *testing.T) {
	t.Run("the dialog's 50% moves the org warning", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "org_alert_50"))
		if b.WarnThreshold != 0.5 {
			t.Fatalf("warn_threshold %v, want 0.5", b.WarnThreshold)
		}
		at60, _ := r7Plugin(t, b)
		r7Spend(t, at60, "60.00", nil)
		if _, rc := r7Ask(at60, nil); rc.Metadata["org_budget_warning"] != "true" {
			t.Fatalf("$60 of $100 at a 50%% threshold: no warning")
		}
		at40, _ := r7Plugin(t, b)
		r7Spend(t, at40, "40.00", nil)
		if _, rc := r7Ask(at40, nil); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$40 of $100 at a 50%% threshold: warned")
		}
	})
	t.Run("a level's own alert threshold moves that level's warning", func(t *testing.T) {
		levels := map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 50, AlertThresholds: []float64{0.6}}}
		eng := map[string]string{"key_team": "eng"}
		over, _ := r7Plugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: levels})
		r7Spend(t, over, "31.00", eng)
		if _, rc := r7Ask(over, eng); rc.Metadata["org_budget_warning"] != "true" {
			t.Fatalf("$31 of $50 at a 60%% level threshold: no warning")
		}
		under, _ := r7Plugin(t, &tenant.BudgetsConfig{Enabled: true, Teams: levels})
		r7Spend(t, under, "29.00", eng)
		if _, rc := r7Ask(under, eng); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$29 of $50 at a 60%% level threshold: warned")
		}
	})
	t.Run("a level without its own thresholds keeps the org default", func(t *testing.T) {
		eng := map[string]string{"key_team": "eng"}
		p, _ := r7Plugin(t, &tenant.BudgetsConfig{Enabled: true, WarnThreshold: 0.9,
			Teams: map[string]*tenant.BudgetLevelConfig{"eng": {Limit: 100}}})
		r7Spend(t, p, "85.00", eng)
		if _, rc := r7Ask(p, eng); rc.Metadata["org_budget_warning"] != "" {
			t.Fatalf("$85 of $100 under the org's 90%% default: warned")
		}
	})
}

func TestR7BudgetHardLimitLatest(t *testing.T) {
	t.Run("a later Hard Limit save wins over an older Organization budget", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "hard_limit_later"))
		p, _ := r7Plugin(t, b)
		r7Spend(t, p, "1.00", nil)
		if res, _ := r7Ask(p, nil); r7Code(res) != "org_budget_exceeded" {
			t.Fatalf("Hard Limit $0.000001 saved last: got %s, want org_budget_exceeded", r7Code(res))
		}
	})
	t.Run("a later Organization save wins over an older Hard Limit", func(t *testing.T) {
		b := r7Decode(t, r7Case(t, "org_limit_later"))
		p, _ := r7Plugin(t, b)
		r7Spend(t, p, "1.00", nil)
		if res, _ := r7Ask(p, nil); res.Error != nil {
			t.Fatalf("Organization $100 saved last: refused %s", res.Error.Code)
		}
	})
}
