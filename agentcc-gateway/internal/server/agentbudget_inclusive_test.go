package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Observe both reserves before dispatch, not their post-response corrections.
// The credits hold is the independent oracle for the run budget's hold.
type inclusiveBudgetProbe struct {
	store        *redisstate.AgentBudgetStore
	credits, run int64
	remaining    *float64
	err          error
}

func (*inclusiveBudgetProbe) Name() string { return "inclusive-budget-probe" }

// Observe after both the run-budget admission and credits reserve.
func (*inclusiveBudgetProbe) Priority() int { return 516 }
func (p *inclusiveBudgetProbe) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.credits = rc.Billing.HoldMicros
	p.remaining = rc.Billing.RunBudgetRemainingUSD
	if rc.Agent.RunID != "" {
		var state redisstate.AgentBudgetState
		state, p.err = p.store.Peek(ctx, "org-m", rc.Agent.RunID)
		p.run = state.HeldMicros
	}
	return pipeline.ResultContinue()
}
func (*inclusiveBudgetProbe) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func inclusiveBudgetGateway(t *testing.T) (*managedTestGateway, *r601Owner, *inclusiveBudgetProbe) {
	t.Helper()
	g, owner, _ := cat2OperatorGateway(t, true)
	prefix := fmt.Sprintf("test:inclusive-budget:%d:", time.Now().UnixNano())
	store := redisstate.NewAgentBudgetStore(r7AgentRedis(t, prefix), prefix)
	h := g.srv.handlers
	ab := agentbudget.New(agentbudget.Deps{
		Store:    store,
		Settings: agentbudget.Settings{MarkupPercent: 5, HoldTTL: time.Minute, ReserveDefaultOutputTokens: 4096},
		Prices:   h.Deps().Prices, MaxOutput: catalogueSpendEstimator(g).MaxOutput, OrgConfig: g.tenants.Get,
	})
	h.SetRunBudget(ab)
	g.credits.SetMaxOutput(catalogueSpendEstimator(g).MaxOutput)
	cost := costplugin.New(true, h.ModelDB, g.tenants, nil)
	cost.SetManagedMarkup(5)
	cost.SetPricer(costplugin.NewPricer(h.ModelDB, func() models.PriceLookup { return h.Deps().Prices }))
	probe := &inclusiveBudgetProbe{store: store}
	*g.srv.engine = *pipeline.NewEngine(cost, g.credits, ab, probe)
	return g, owner, probe
}

// All 29 live inclusive listings, at both output caps, in both stream modes.
// The router family keeps its BYOK prices but has no inclusive card on credits.
// Each cap is ONE MICRO below the real credits hold: inclusive requests must
// never be clamped down to a cheaper scalar-token estimate. The exact-bound
// control reaches the real loopback adapter and observes both in-flight holds.
func TestAgentBudgetInclusiveHTTP(t *testing.T) {
	g, owner, probe := inclusiveBudgetGateway(t)
	var ids []string
	for _, row := range g.srv.handlers.managedModelObjects() {
		card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", row.ID, "")
		if ok && card.Inclusive != nil {
			ids = append(ids, row.ID)
		}
	}
	if len(ids) != 29 {
		t.Fatalf("inclusive listings=%d want 29", len(ids))
	}
	for _, id := range ids {
		for _, out := range []int{64, 1024} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/out=%d/stream=%t", id, out, stream), func(t *testing.T) {
					g.tenants.Set("org-m", managedOptIn())
					req := catalogueSpendRequest(id, 0, out, 1)
					req.Stream = stream
					want := assertCatalogueSpendParity(t, g, req)
					if want <= 1 {
						t.Fatalf("credits hold=%d is not a positive inclusive bound", want)
					}
					body, err := json.Marshal(req)
					if err != nil {
						t.Fatal(err)
					}
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, name string) {
						card, _ := g.srv.handlers.catalog.Load().PriceCard("openrouter", name, "")
						if card.Inclusive != nil && card.Inclusive.RouterUniverse != "" {
							name = "openai/o1-pro"
						}
						r601Response("0.0000095", stream)(w, r, name)
					})
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)

					t.Run("exact-bound", func(t *testing.T) {
						run := fmt.Sprintf("exact-%d", time.Now().UnixNano())
						before := g.managed.calls()
						w := g.chat(t, string(body), map[string]string{"x-agentcc-run-id": run, "x-agentcc-budget-usd": r7USD(want)})
						if w.Code != http.StatusOK || g.managed.calls()-before != 1 {
							t.Fatalf("exact bound: status=%d calls=%d body=%.300s", w.Code, g.managed.calls()-before, w.Body.String())
						}
						if probe.err != nil || probe.credits != want || probe.run != want {
							t.Errorf("in-flight credits=%d run=%d want=%d err=%v", probe.credits, probe.run, want, probe.err)
						}
						if probe.remaining == nil || *probe.remaining != 0 {
							t.Errorf("in-flight remaining=%v, want zero", probe.remaining)
						}
						// Streaming commits headers before settlement; a buffered
						// response reports the budget after the 11-micro charge.
						remaining := r7USD(want - 11)
						if stream {
							remaining = "0.000000"
						}
						if w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "" || w.Header().Get(agentbudget.HeaderBudgetRemaining) != remaining {
							t.Errorf("exact-bound headers=%v want remaining=%s", w.Header(), remaining)
						}
						state, err := probe.store.Peek(context.Background(), "org-m", run)
						if err != nil || state.HeldMicros != 0 || state.SpentMicros != 11 {
							t.Errorf("settled run=%+v err=%v; want spent=11 held=0", state, err)
						}
					})

					for _, kind := range []string{"max-cost", "remaining", "org-cap", "org-cap-no-run", "run-budget"} {
						t.Run(kind, func(t *testing.T) {
							cfg := managedOptIn()
							headers := map[string]string{}
							run := fmt.Sprintf("refuse-%d", time.Now().UnixNano())
							code, source := agentbudget.CodeCallCapExceeded, models.LimitSourceCallCap
							switch kind {
							case "max-cost":
								headers["x-agentcc-max-cost-usd"] = r7USD(want - 1)
							case "remaining":
								headers["x-agentcc-budget-remaining-usd"] = r7USD(want - 1)
							case "org-cap", "org-cap-no-run":
								capUSD := float64(want-1) / 1e6
								cfg.ManagedModels.MaxCallCostUSD = &capUSD
								if kind == "org-cap" {
									headers["x-agentcc-run-id"] = run
								}
							case "run-budget":
								headers["x-agentcc-run-id"], headers["x-agentcc-budget-usd"] = run, r7USD(want-1)
								code, source = agentbudget.CodeBudgetExhausted, models.LimitSourceAgentBudget
							}
							g.tenants.Set("org-m", cfg)
							defer g.tenants.Set("org-m", managedOptIn())
							before := g.account(t, "org-m")
							events, calls := len(owner.Outbox("org-m")), g.managed.calls()
							w := g.chat(t, string(body), headers)
							got, meta := r7ErrorMeta(t, w.Body.Bytes())
							if w.Code != http.StatusPaymentRequired || got != code || meta["limit_source"] != source {
								t.Errorf("status=%d code=%q want=%s body=%.300s", w.Code, got, code, w.Body.String())
							} else {
								if meta["required_usd"] != float64(want)/1e6 || meta["available_usd"] != float64(want-1)/1e6 {
									t.Errorf("cap amounts disagree with credits hold: %v", meta)
								}
								_, scoped := meta["scope"]
								if scoped != (headers["x-agentcc-run-id"] != "") {
									t.Errorf("scope disclosure: %v", meta)
								}
							}
							after, afterEvents := g.account(t, "org-m"), len(owner.Outbox("org-m"))
							if g.managed.calls() != calls || !reflect.DeepEqual(before, after) || afterEvents != events {
								t.Errorf("refusal reached provider or changed credits ledger: calls=%d before=%+v after=%+v outbox=%d->%d", g.managed.calls()-calls, before, after, events, afterEvents)
							}
							if w.Header().Get(agentbudget.HeaderMaxTokensClamped) != "" || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") || w.Header().Get("Retry-After") != "" {
								t.Errorf("refusal must be unclamped JSON before streaming: %v", w.Header())
							}
							state, err := probe.store.Peek(context.Background(), "org-m", run)
							if err != nil || state.HeldMicros != 0 || state.SpentMicros != 0 {
								t.Errorf("refused run changed: %+v %v", state, err)
							}
						})
					}
					t.Logf("HTTP inclusive budget model=%s output=%d stream=%t hold=%d", id, out, stream, want)
				})
			}
		}
	}
}
