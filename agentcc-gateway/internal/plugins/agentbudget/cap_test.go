package agentbudget

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A call over its cap is clamped to fit when that leaves at least 256 output
// tokens, and refused below that: agent_call_cap_exceeded when a per-call cap
// binds, agent_budget_exhausted when the run's budget does.
func TestCallCapClampAt256Boundary(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardTokens, nil)
	// "hi" is 8 input tokens: 8 × $1/M + n × $10/M, so 256 tokens cost
	// 2,568 micros and 255 tokens 2,558.
	for _, c := range []struct {
		name               string
		maxCost, budget    float64
		completionOnly     bool
		wantClamp          int
		wantCode, wantFrom string
	}{
		{name: "per-call cap fits 256", maxCost: 0.002568, budget: -1, wantClamp: 256},
		{name: "per-call cap fits 255", maxCost: 0.002567, budget: -1, wantCode: CodeCallCapExceeded, wantFrom: models.LimitSourceCallCap},
		{name: "run budget fits 256", maxCost: -1, budget: 0.002568, wantClamp: 256},
		{name: "run budget fits 255", maxCost: -1, budget: 0.002567, wantCode: CodeBudgetExhausted, wantFrom: models.LimitSourceAgentBudget},
		{name: "max_completion_tokens clamped", maxCost: 0.005, budget: -1, completionOnly: true, wantClamp: 499},
		{name: "fits as sent", maxCost: 0.02, budget: -1},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc := inRun(call(t, 1000), "run-"+t.Name(), c.budget)
			if c.completionOnly {
				rc.Request.MaxCompletionTokens, rc.Request.MaxTokens = rc.Request.MaxTokens, nil
			}
			if c.maxCost >= 0 {
				rc.Agent.MaxCostUSD = &c.maxCost
			}
			apiErr := admit(p, rc, nil)
			if c.wantCode != "" {
				meta := wantRefusal(t, apiErr, http.StatusPaymentRequired, c.wantCode, c.wantFrom)
				if meta["required_usd"] != 0.010008 {
					t.Fatalf("required_usd %v, want the unclamped worst case 0.010008", meta["required_usd"])
				}
				if *rc.Request.MaxTokens != 1000 {
					t.Fatalf("a refused call's max_tokens changed to %d", *rc.Request.MaxTokens)
				}
				return
			}
			if apiErr != nil {
				t.Fatalf("refused: %v", apiErr)
			}
			got := rc.Request.MaxTokens
			if c.completionOnly {
				if got != nil {
					t.Fatalf("max_tokens set to %d on a max_completion_tokens call", *got)
				}
				got = rc.Request.MaxCompletionTokens
			}
			if c.wantClamp == 0 {
				if *got != 1000 || header(rc, HeaderMaxTokensClamped) != "" {
					t.Fatalf("a call within its cap was clamped to %d", *got)
				}
				return
			}
			if *got != c.wantClamp || header(rc, HeaderMaxTokensClamped) != fmt.Sprint(c.wantClamp) {
				t.Fatalf("clamped to %d (header %q), want %d", *got, header(rc, HeaderMaxTokensClamped), c.wantClamp)
			}
		})
	}
}

// The hook sets rc.Billing.CallCapUSD to the lowest present term:
// max-cost-usd, budget-remaining-usd, the scope's room and the org's
// managed_models.max_call_cost_usd.
func TestCallCapIsTheLowestTerm(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	f := func(v float64) *float64 { return &v }
	maxCall := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{MaxCallCostUSD: f(0.03)}}
	for _, c := range []struct {
		name                       string
		maxCost, remaining, budget *float64
		cfg                        *tenant.OrgConfig
		want                       *float64
	}{
		{name: "no term", want: nil},
		{name: "max-cost-usd", maxCost: f(0.04), want: f(0.04)},
		{name: "budget-remaining-usd", maxCost: f(0.04), remaining: f(0.035), want: f(0.035)},
		{name: "org max_call_cost_usd", maxCost: f(0.04), cfg: maxCall, want: f(0.03)},
		{name: "scope room", maxCost: f(0.04), budget: f(0.02), cfg: maxCall, want: f(0.02)},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc := inRun(call(t, 0), "run-"+t.Name(), -1)
			rc.Agent.MaxCostUSD, rc.Agent.BudgetRemainingUSD, rc.Agent.BudgetUSD = c.maxCost, c.remaining, c.budget
			if apiErr := p.CallCap(rc, c.cfg); apiErr != nil {
				t.Fatal(apiErr)
			}
			got := rc.Billing.CallCapUSD
			if (got == nil) != (c.want == nil) || (got != nil && *got != *c.want) {
				t.Fatalf("CallCapUSD %v, want %v", deref(got), deref(c.want))
			}
		})
	}
}

func deref(p *float64) any {
	if p == nil {
		return nil
	}
	return *p
}

// An unpriced model under a cap is refused with call_cap_unenforceable unless
// routing.agents.unpriced_under_cap=allow; with no cap it is held at 0.
func TestCallCapUnpricedUnderCap(t *testing.T) {
	store, _, _ := testStore(t)
	allow := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{UnpricedUnderCap: "allow"}}}
	for _, c := range []struct {
		name   string
		budget float64
		cfg    *tenant.OrgConfig
		want   string
	}{{"capped", 0.05, nil, CodeCallCapUnenforceable}, {"capped allowed", 0.05, allow, ""}, {"no cap", -1, nil, ""}} {
		t.Run(c.name, func(t *testing.T) {
			p := testPlugin(store, cardTokens, c.cfg)
			rc := inRun(call(t, 0), "run-"+t.Name(), c.budget)
			rc.Request.Model = "unpriced"
			apiErr := admit(p, rc, c.cfg)
			if c.want != "" {
				wantRefusal(t, apiErr, http.StatusPaymentRequired, c.want, models.LimitSourceCallCap)
				return
			}
			if apiErr != nil {
				t.Fatal(apiErr)
			}
			if st := peek(t, store, "run-"+t.Name()); st.HeldMicros != 0 {
				t.Fatalf("an unpriced call held %d", st.HeldMicros)
			}
		})
	}
}
