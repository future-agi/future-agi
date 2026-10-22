package agentbudget

import (
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A caller outside the pipeline reserves, settles and voids a run
// scope by its own hold id, with no request context; the same hold id twice
// is one hold, and a refused hold carries the plugin's 402s.
func TestScopeStoreUsableOutsidePipeline(t *testing.T) {
	store, _, _ := testStore(t)
	s := NewScopes(store, 0)
	opts := ScopeOptions{LimitMicros: 50_000}
	for i := 0; i < 2; i++ {
		st, refusal, err := s.Reserve(bg, testOrg, "run-ext", "tool-call-1", 10_000, opts)
		if err != nil || refusal != nil || st.HeldMicros != 10_000 {
			t.Fatalf("reserve %d: %+v, %v, %v", i, st, refusal, err)
		}
	}
	st, err := s.Settle(bg, testOrg, "run-ext", "tool-call-1", 8_000, 0)
	if err != nil || st.SpentMicros != 8_000 || st.HeldMicros != 0 {
		t.Fatalf("settle: %+v, %v", st, err)
	}
	if st, err = s.Void(bg, testOrg, "run-ext", "tool-call-1", 0); err != nil || st.SpentMicros != 8_000 || st.HeldMicros != 0 {
		t.Fatalf("void after settle: %+v, %v", st, err)
	}
	if _, refusal, err := s.Reserve(bg, testOrg, "run-ext", "tool-call-2", 5_000, opts); err != nil || refusal != nil {
		t.Fatalf("second hold: %v, %v", refusal, err)
	}
	if st, err = s.Void(bg, testOrg, "run-ext", "tool-call-2", 0); err != nil || st.HeldMicros != 0 || st.SpentMicros != 8_000 {
		t.Fatalf("void: %+v, %v", st, err)
	}
	_, refusal, err := s.Reserve(bg, testOrg, "run-ext", "tool-call-3", 42_001, opts)
	if err != nil {
		t.Fatal(err)
	}
	meta := wantRefusal(t, refusal, http.StatusPaymentRequired, CodeBudgetExhausted, models.LimitSourceAgentBudget)
	if meta["available_usd"] != 0.042 || meta["required_usd"] != 0.042001 {
		t.Fatalf("exhausted metadata %v", meta)
	}
	if err := store.Stop(bg, testOrg, "run-ext", "tool_call_blocked:shell", time.Minute); err != nil {
		t.Fatal(err)
	}
	_, refusal, _ = s.Reserve(bg, testOrg, "run-ext", "tool-call-4", 1, opts)
	wantRefusal(t, refusal, http.StatusPaymentRequired, CodeRunStopped, models.LimitSourceRunStop)
	stopped, err := s.Stopped(bg, testOrg, "run-ext")
	if err != nil || stopped == nil || stopped.Code != CodeRunStopped {
		t.Fatalf("Stopped: %v, %v", stopped, err)
	}
}

// Eight members of one run, each worth $0.01, on a $0.05 run budget at once:
// five are held, three refused with agent_budget_exhausted, and the scope
// never holds more than its limit.
func TestAgentBudgetConcurrentMembers(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	var mu sync.Mutex
	codes := map[string]int{}
	var wg sync.WaitGroup
	start := make(chan struct{})
	rcs := make([]*models.RequestContext, 8)
	for i := range rcs {
		rcs[i] = inRun(call(t, 0), "society-1", 0.05)
	}
	for _, rc := range rcs {
		wg.Add(1)
		go func(rc *models.RequestContext) {
			defer wg.Done()
			<-start
			code := "held"
			if apiErr := admit(p, rc, nil); apiErr != nil {
				code = apiErr.Code
			}
			mu.Lock()
			codes[code]++
			mu.Unlock()
		}(rc)
	}
	close(start)
	wg.Wait()
	if codes["held"] != 5 || codes[CodeBudgetExhausted] != 3 {
		t.Fatalf("outcomes %v, want 5 held and 3 agent_budget_exhausted", codes)
	}
	if st := peek(t, store, "society-1"); st.HeldMicros != 50_000 || st.LimitMicros != 50_000 {
		t.Fatalf("scope %+v, want $0.05 held of $0.05", st)
	}
	for _, rc := range rcs {
		rc.RunFinalizers() // a refused member holds nothing; a held one is voided
	}
	if st := peek(t, store, "society-1"); st.HeldMicros != 0 || st.SpentMicros != 0 {
		t.Fatalf("after every member ended: %+v", st)
	}
}

// Two calls of one attempt (x-agentcc-attempt-id) share one hold.
func TestAgentBudgetAttemptIDReusesHold(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	for i := 0; i < 2; i++ {
		rc := inRun(call(t, 0), "run-att", 0.05)
		rc.Agent.AttemptID = "att-1"
		if apiErr := admit(p, rc, nil); apiErr != nil {
			t.Fatalf("call %d: %v", i, apiErr)
		}
	}
	if st := peek(t, store, "run-att"); st.HeldMicros != 10_000 {
		t.Fatalf("held %d after one attempt sent twice, want one $0.01 hold", st.HeldMicros)
	}
}

// The first budget a run sends is its limit; a lower one lowers it, a higher
// one is ignored.
func TestAgentBudgetLowerLimitWins(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	for i, c := range []struct {
		sent float64
		want int64
	}{{0.05, 50_000}, {0.02, 20_000}, {0.08, 20_000}, {-1, 20_000}} {
		rc := inRun(call(t, 0), "run-limit", c.sent)
		if apiErr := admit(p, rc, nil); apiErr != nil {
			t.Fatalf("call %d: %v", i, apiErr)
		}
		rc.RunFinalizers() // voids the hold
		if st := peek(t, store, "run-limit"); st.LimitMicros != c.want {
			t.Fatalf("call %d sent %v: limit %d, want %d", i, c.sent, st.LimitMicros, c.want)
		}
	}
}

// A managed call settles at what it was billed, a BYOK call at its upstream
// cost (each attempt by its own credential), and a BYOK call with no upstream
// cost at its catalog worst case, marked estimated.
func TestAgentBudgetSettlesAtBilledOrUpstreamCost(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardTokens, nil)
	for _, c := range []struct {
		name      string
		setup     func(rc *models.RequestContext)
		want      int64
		estimated bool
	}{
		{name: "byok upstream", setup: func(rc *models.RequestContext) { rc.Billing.UpstreamMicros = 1234 }, want: 1234},
		{name: "managed billed", setup: func(rc *models.RequestContext) {
			rc.Metadata["billing_mode"] = models.BillingModeManaged
			rc.Billing.UpstreamMicros, rc.Billing.BilledMicros = 1000, 1050
		}, want: 1050},
		{name: "mixed attempts", setup: func(rc *models.RequestContext) {
			rc.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK, Billable: true, UpstreamCostMicros: 500, BilledCostMicros: 500})
			rc.AddAttempt(models.Attempt{CredentialType: models.CredentialManaged, Billable: true, UpstreamCostMicros: 600, BilledCostMicros: 630})
			rc.AddAttempt(models.Attempt{CredentialType: models.CredentialManaged, UpstreamCostMicros: 900})
		}, want: 1130},
		{name: "byok with no upstream cost", setup: func(*models.RequestContext) {}, want: 10_008, estimated: true},
		{name: "byok failed", setup: func(rc *models.RequestContext) { rc.AddError(fmt.Errorf("upstream 503")) }, want: 0},
		{name: "cache hit", setup: func(rc *models.RequestContext) { rc.Flags.CacheHit, rc.Billing.UpstreamMicros = true, 999 }, want: 0},
	} {
		t.Run(c.name, func(t *testing.T) {
			scope := "run-" + t.Name()
			rc := inRun(call(t, 1000), scope, 1)
			if apiErr := admit(p, rc, nil); apiErr != nil {
				t.Fatal(apiErr)
			}
			c.setup(rc)
			p.ProcessResponse(bg, rc)
			st := peek(t, store, scope)
			if st.SpentMicros != c.want || st.HeldMicros != 0 {
				t.Fatalf("spent %d held %d, want spent %d and nothing held", st.SpentMicros, st.HeldMicros, c.want)
			}
			if c.estimated != (rc.Metadata["usage_estimated"] == "true") || c.estimated != rc.Billing.UsageEstimated {
				t.Fatalf("usage_estimated %q / %v, want %v", rc.Metadata["usage_estimated"], rc.Billing.UsageEstimated, c.estimated)
			}
			wantRemaining := fmt.Sprintf("%.6f", float64(1_000_000-c.want)/1e6)
			if got := header(rc, HeaderBudgetRemaining); got != wantRemaining || *rc.Billing.RunBudgetRemainingUSD != float64(1_000_000-c.want)/1e6 {
				t.Fatalf("remaining header %q, want %s", got, wantRemaining)
			}
			rc.RunFinalizers()
			if st := peek(t, store, scope); st.SpentMicros != c.want {
				t.Fatalf("the finalizer changed spent to %d", st.SpentMicros)
			}
		})
	}
}

// A call with no run scope that sends no cap is untouched: no cap, no hold,
// no header, no key. One that sends a cap (max-cost-usd, budget-remaining-usd
// or budget-usd) is capped on its own, with nothing held and no key written.
func TestAgentBudgetNoScopeNoEffect(t *testing.T) {
	store, rdb, prefix := testStore(t)
	p := testPlugin(store, cardTokens, nil)
	rc := call(t, 1000)
	if apiErr := admit(p, rc, nil); apiErr != nil {
		t.Fatalf("refused: %v", apiErr)
	}
	rc.Billing.UpstreamMicros = 5000
	p.ProcessResponse(bg, rc)
	if rc.Billing.CallCapUSD != nil || rc.Billing.RunBudgetRemainingUSD != nil || len(rc.ResponseHeaders()) != 0 || *rc.Request.MaxTokens != 1000 {
		t.Fatalf("a call with no scope was changed: cap %v, remaining %v, headers %v",
			deref(rc.Billing.CallCapUSD), deref(rc.Billing.RunBudgetRemainingUSD), rc.ResponseHeaders())
	}
	if keys := scanKeys(rdb, prefix); len(keys) != 0 {
		t.Fatalf("a call with no scope wrote %v", keys)
	}
}

// A call outside any run that sends a cap is capped on its own,
// through the same fit as a run's member (clamped to the largest max_tokens
// of at least 256 that fits, else refused), with nothing held, no key and no
// remaining-budget header. Its refusals name no scope.
func TestAgentBudgetUnscopedCallCap(t *testing.T) {
	store, rdb, prefix := testStore(t)
	p := testPlugin(store, cardTokens, nil)
	// cardTokens: $1/M prompt, $10/M completion; 1000 output tokens is $0.01.
	usd := func(v float64) *float64 { return &v }
	for _, c := range []struct {
		name            string
		maxCost, remain *float64
		budget          *float64
		wantCode        string // "" admitted
		wantClamp       int    // 0 unchanged
	}{
		{"max-cost over the worst case", usd(1), nil, nil, "", 0},
		{"max-cost clamps max_tokens", usd(0.005), nil, nil, "", 499},
		{"max-cost below 256 output tokens", usd(0.000001), nil, nil, CodeCallCapExceeded, 0},
		{"budget-remaining 0", nil, usd(0), nil, CodeCallCapExceeded, 0},
		{"budget-usd covers the call", nil, nil, usd(1), "", 0},
		{"budget-usd below 256 output tokens", nil, nil, usd(0.000001), CodeBudgetExhausted, 0},
		{"the lowest term binds", usd(1), usd(0.000001), usd(1), CodeCallCapExceeded, 0},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc := call(t, 1000)
			rc.Agent.MaxCostUSD, rc.Agent.BudgetRemainingUSD, rc.Agent.BudgetUSD = c.maxCost, c.remain, c.budget
			apiErr := admit(p, rc, nil)
			if c.wantCode != "" {
				meta := wantRefusal(t, apiErr, http.StatusPaymentRequired, c.wantCode, "")
				if _, ok := meta["scope"]; ok {
					t.Fatalf("a call outside any run named a scope: %v", meta)
				}
				return
			}
			if apiErr != nil {
				t.Fatalf("refused: %v", apiErr)
			}
			if rc.Billing.CallCapUSD == nil {
				t.Fatal("no call cap set")
			}
			if c.wantClamp != 0 {
				if got := header(rc, HeaderMaxTokensClamped); got != fmt.Sprint(c.wantClamp) || *rc.Request.MaxTokens != c.wantClamp {
					t.Fatalf("clamped header %q, max_tokens %d, want %d", got, *rc.Request.MaxTokens, c.wantClamp)
				}
			} else if *rc.Request.MaxTokens != 1000 {
				t.Fatalf("max_tokens changed to %d", *rc.Request.MaxTokens)
			}
			rc.Billing.UpstreamMicros = 5000
			p.ProcessResponse(bg, rc)
			if got := header(rc, HeaderBudgetRemaining); got != "" || rc.Billing.RunBudgetRemainingUSD != nil {
				t.Fatalf("a call outside any run reported a run's remaining budget %q", got)
			}
		})
	}
	if keys := scanKeys(rdb, prefix); len(keys) != 0 {
		t.Fatalf("calls outside any run wrote %v", keys)
	}
	// Without Redis a call outside any run is capped the same: nothing is held.
	np := testPlugin(nil, cardTokens, nil)
	rc := call(t, 1000)
	rc.Agent.MaxCostUSD = usd(0.000001)
	wantRefusal(t, admit(np, rc, nil), http.StatusPaymentRequired, CodeCallCapExceeded, models.LimitSourceCallCap)
}

// A runtime token's budget scope and budget are authoritative: a header can
// lower the budget, never raise it or move the call to another scope.
func TestAgentBudgetRuntimeClaimLimitsCannotBeRaisedByHeader(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	claim, raised, lowered := 0.015, 0.05, 0.012
	for i, c := range []struct {
		sent       *float64
		claimScope string
		scope      string
		limit      int64
	}{
		{&raised, "", "root-1", 15_000},                     // a higher header is ignored
		{nil, "", "root-1", 15_000},                         // the claim alone
		{&lowered, "", "root-1", 12_000},                    // a lower header lowers it
		{&raised, "claimed-scope", "claimed-scope", 15_000}, // budget_scope claim, not root_run_id
	} {
		rc := call(t, 0)
		rc.Runtime = &models.RuntimeIdentity{RootRunID: "root-1", BudgetScope: c.claimScope, BudgetUSD: &claim}
		rc.Agent.BudgetScope, rc.Agent.BudgetUSD = "header-scope", c.sent
		if apiErr := admit(p, rc, nil); apiErr != nil {
			t.Fatalf("call %d: %v", i, apiErr)
		}
		if st := peek(t, store, c.scope); st.LimitMicros != c.limit || st.HeldMicros != 10_000 {
			t.Fatalf("call %d: scope %s %+v, want limit %d and its $0.01 held", i, c.scope, st, c.limit)
		}
		rc.RunFinalizers()
	}
	// The claim's $0.015 cannot hold $0.02, whatever the header says.
	big := testPlugin(store, models.PriceCard{Request: 0.02}, nil)
	rc := call(t, 0)
	rc.Runtime = &models.RuntimeIdentity{RootRunID: "root-2", BudgetUSD: &claim}
	rc.Agent.BudgetUSD = &raised
	wantRefusal(t, admit(big, rc, nil), http.StatusPaymentRequired, CodeBudgetExhausted, models.LimitSourceAgentBudget)
	if st := peek(t, store, "header-scope"); st.HeldMicros != 0 || st.HasLimit() {
		t.Fatalf("the header scope was used: %+v", st)
	}
}

// A stopped run refuses every later call with agent_run_stopped and the
// stop's reason, before routing and at the reserve.
func TestAgentBudgetStoppedKeyRefuses(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	if err := store.Stop(bg, testOrg, "run-stop", "tool_call_blocked:shell", time.Minute); err != nil {
		t.Fatal(err)
	}
	rc := inRun(call(t, 0), "run-stop", -1)
	meta := wantRefusal(t, p.CallCap(rc, nil), http.StatusPaymentRequired, CodeRunStopped, models.LimitSourceRunStop)
	if meta["reason"] != "tool_call_blocked:shell" || meta["scope"] != "run:run-stop" {
		t.Fatalf("metadata %v", meta)
	}
	emb := inRun(call(t, 0), "run-stop", -1)
	emb.EndpointType, emb.Request = "embedding", nil
	wantRefusal(t, p.ProcessRequest(bg, emb).Error, http.StatusPaymentRequired, CodeRunStopped, models.LimitSourceRunStop)
}

// Every run-budget refusal carries scope run:<scope> and resets_at null.
func TestAgentBudgetRefusalsCarryScopeAndReset(t *testing.T) {
	t.Run("TestLimitErrorCarriesScopeAndReset", TestLimitErrorCarriesScopeAndReset)
}

func TestLimitErrorCarriesScopeAndReset(t *testing.T) {
	for _, err := range []*models.APIError{
		ErrBudgetExhausted("L1", 10_000, 2_000),
		ErrBudgetExhausted("L1", -1, 0),
		ErrCallCapExceeded("L1", 10_000, 2_000),
		ErrCallCapUnenforceable("L1", "m"),
		ErrRunStopped("L1", "tool_call_blocked:shell"),
	} {
		meta := wantRefusal(t, err, http.StatusPaymentRequired, err.Code, "")
		if meta["scope"] != "run:L1" {
			t.Errorf("%s: scope %v, want run:L1", err.Code, meta["scope"])
		}
		if v, ok := meta["resets_at"]; !ok || v != nil {
			t.Errorf("%s: resets_at %v (present %v), want null", err.Code, v, ok)
		}
	}
}

// When the scope store fails, a call in a run is refused with 503
// budget_state_unavailable (budgets.fail_mode closed, the default) or
// admitted unheld (open). Without a store, a call that sends no budget has
// nothing to enforce; one that sends a budget fails the same way.
func TestAgentBudgetFailMode(t *testing.T) {
	open := &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{FailMode: "open"}}
	down := redisstate.NewAgentBudgetStore(nil, "")
	for _, c := range []struct {
		name   string
		store  *redisstate.AgentBudgetStore
		cfg    *tenant.OrgConfig
		budget float64
		want   bool // refused
	}{
		{"redis down, closed", down, nil, -1, true},
		{"redis down, open", down, open, 0.05, false},
		{"no store, no budget", nil, nil, -1, false},
		{"no store, budget", nil, nil, 0.05, true},
		{"no store, budget, open", nil, open, 0.05, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			p := testPlugin(c.store, cardFee10k, c.cfg)
			rc := inRun(call(t, 0), "run-f", c.budget)
			apiErr := admit(p, rc, c.cfg)
			if !c.want {
				if apiErr != nil {
					t.Fatalf("refused: %v", apiErr)
				}
				p.ProcessResponse(bg, rc)
				return
			}
			meta := wantRefusal(t, apiErr, http.StatusServiceUnavailable, CodeBudgetStateUnavailable, "")
			if meta["scope"] != "run:run-f" {
				t.Fatalf("metadata %v", meta)
			}
		})
	}
	// The reserve fails closed too, for a call that reaches it without the hook.
	p := testPlugin(down, cardFee10k, nil)
	rc := inRun(call(t, 0), "run-f", -1)
	rc.EndpointType, rc.Request = "embedding", nil
	wantRefusal(t, p.ProcessRequest(bg, rc).Error, http.StatusServiceUnavailable, CodeBudgetStateUnavailable, "")
}

// A hold nothing settled (an exit path with no post-plugins) is voided when
// the request ends.
func TestAgentBudgetFinalizerVoidsUnsettledHold(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	rc := inRun(call(t, 0), "run-exit", 0.05)
	if apiErr := admit(p, rc, nil); apiErr != nil {
		t.Fatal(apiErr)
	}
	if st := peek(t, store, "run-exit"); st.HeldMicros != 10_000 {
		t.Fatalf("held %d", st.HeldMicros)
	}
	rc.RunFinalizers()
	if st := peek(t, store, "run-exit"); st.HeldMicros != 0 || st.SpentMicros != 0 {
		t.Fatalf("after the request ended: %+v", st)
	}
	if _, ok := p.states.Load(rc); ok {
		t.Fatal("the call's state outlived its request")
	}
}

// A run whose spend reached its limit (here after an overshoot) refuses the
// next call before routing; embedding and rerank calls count in the scope,
// capability calls do not (they hold through Scopes themselves).
func TestAgentBudgetExhaustedRunAndCountedEndpoints(t *testing.T) {
	store, _, _ := testStore(t)
	p := testPlugin(store, cardFee10k, nil)
	for _, endpoint := range []string{"embedding", "rerank", "search"} {
		rc := inRun(call(t, 0), "run-over", 0.02)
		rc.EndpointType, rc.Request = endpoint, nil
		if apiErr := p.ProcessRequest(bg, rc).Error; apiErr != nil {
			t.Fatalf("%s: %v", endpoint, apiErr)
		}
		rc.Billing.UpstreamMicros = 15_000
		p.ProcessResponse(bg, rc)
	}
	if st := peek(t, store, "run-over"); st.SpentMicros != 30_000 {
		t.Fatalf("spent %d, want the embedding and rerank calls only (30000)", st.SpentMicros)
	}
	rc := inRun(call(t, 0), "run-over", -1)
	meta := wantRefusal(t, p.CallCap(rc, nil), http.StatusPaymentRequired, CodeBudgetExhausted, models.LimitSourceAgentBudget)
	if _, sent := meta["required_usd"]; sent || meta["available_usd"] != 0.0 {
		t.Fatalf("metadata %v, want available 0 and no required", meta)
	}
}

// A call with no run scope costs the installed plugin nothing: no allocation
// in the hook, the reserve or the settle (non-agent traffic keeps its allocs
// budget of 0).
func TestAgentBudgetUnscopedCallAllocatesNothing(t *testing.T) {
	p := testPlugin(redisstate.NewAgentBudgetStore(nil, ""), cardTokens, nil)
	rc := call(t, 1000)
	if n := testing.AllocsPerRun(100, func() {
		_ = p.CallCap(rc, nil)
		_ = p.ProcessRequest(bg, rc)
		_ = p.ProcessResponse(bg, rc)
	}); n != 0 {
		t.Fatalf("%v allocations per unscoped call, want 0", n)
	}
}
