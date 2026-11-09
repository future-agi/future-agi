package server

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type capLateRewrite struct{ on bool }

func (*capLateRewrite) Name() string  { return "cap-late-rewrite" }
func (*capLateRewrite) Priority() int { return 600 }
func (p *capLateRewrite) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if p.on {
		rc.Request.Messages = append(rc.Request.Messages, models.Message{Role: "system", Content: json.RawMessage(`"expanded after wallet reservation"`)})
	}
	return pipeline.ResultContinue()
}
func (*capLateRewrite) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func capComposedGateway(t *testing.T, extra ...pipeline.Plugin) (*managedTestGateway, *redisstate.Client, string) {
	t.Helper()
	db, e := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	if e != nil {
		t.Fatal(e)
	}
	client, e := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: db, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	prefix := fmt.Sprintf("test:g8-compose:%d:", time.Now().UnixNano())
	t.Cleanup(func() {
		it := client.Redis().Scan(context.Background(), 0, prefix+"*", 100).Iterator()
		for it.Next(context.Background()) {
			client.Redis().Del(context.Background(), it.Val())
		}
		client.Close()
	})
	p := capbudget.New(nil, nil, false, nil)
	p.SetCapStore(redisstate.NewCapHoldStore(client, prefix))
	var g *managedTestGateway
	run := agentbudget.New(agentbudget.Deps{Store: redisstate.NewAgentBudgetStore(client, prefix),
		Settings:  agentbudget.Settings{MarkupPercent: 5, HoldTTL: time.Hour, ReserveDefaultOutputTokens: 4096},
		OrgConfig: func(org string) *tenant.OrgConfig { return g.tenants.Get(org) },
	})
	g = newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: append([]pipeline.Plugin{p, run}, extra...)})
	g.srv.handlers.SetRunBudget(run)
	t.Cleanup(func() { g.srv.handlers.SetRunBudget(nil) })
	key := g.srv.keyStore.Authenticate(managedOrgKey)
	cfg := managedOptIn()
	limit := 1.0
	cfg.Meta = &tenant.ConfigMeta{Version: 1}
	cfg.Budgets = &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 1, NoMaxTokens: "clamp", Agents: map[string]*tenant.AdmissionCapConfig{"worker": {Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}}, KeyIDs: map[string]*tenant.AdmissionCapConfig{key.ID: {Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}}}
	cfg.Routing = &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{key.ID: "worker"}}}
	g.tenants.Set("org-m", cfg)
	g.grant(t, "org-m", 1_000_000)
	return g, client, prefix
}

func TestCapWalletOutboxAndForwardedRequest(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("strict priced chat: %d %s", w.Code, w.Body)
	}
	if g.managed.calls() != 1 {
		t.Fatalf("provider calls=%d", g.managed.calls())
	}
	account := g.account(t, "org-m")
	if account.SpentMicros != 7350 || account.HeldMicros != 0 || len(g.outbox("org-m")) != 1 {
		t.Fatalf("money path account=%+v outbox=%v", account, g.outbox("org-m"))
	}
	state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
	if got := client.Redis().HGet(context.Background(), state, "spent:agent:worker").Val(); got != "7350" {
		t.Fatalf("cap actual=%s", got)
	}
}

func TestCapBYOKWalletIsolation(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
	g.tenants.Set("org-m", cfg)
	w := g.chat(t, chatBody("gpt-4o"), nil)
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-billing-mode") != "byok" || g.operator.calls() != 1 {
		t.Fatalf("BYOK: %d %s", w.Code, w.Body)
	}
	account := g.account(t, "org-m")
	if account.SpentMicros != 0 || account.HeldMicros != 0 || len(g.outbox("org-m")) != 0 {
		t.Fatalf("BYOK debited wallet: %+v", account)
	}
	usd, err := strconv.ParseFloat(w.Header().Get("x-agentcc-cost"), 64)
	if err != nil || usd <= 0 {
		t.Fatalf("BYOK cost missing: %s %v", w.Header().Get("x-agentcc-cost"), err)
	}
	state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
	if got := client.Redis().HGet(context.Background(), state, "spent:agent:worker").Val(); got != strconv.FormatInt(int64(math.Round(usd*1e6)), 10) {
		t.Fatalf("BYOK cap=%s cost=%f", got, usd)
	}
}

func TestCapActualOvershootPreservesWalletDebit(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cost := 0.2
	g.managed.reportedCost.Store(&cost)
	w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("overshoot: %d %s", w.Code, w.Body)
	}
	if a := g.account(t, "org-m"); a.SpentMicros != 210000 || a.HeldMicros != 0 || len(g.outbox("org-m")) != 1 {
		t.Fatalf("actual debit clipped: %+v", a)
	}
	state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
	if got := client.Redis().HGet(context.Background(), state, "spent:agent:worker").Val(); got != "210000" {
		t.Fatalf("cap actual clipped: %s", got)
	}
	later := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if later.Code != http.StatusServiceUnavailable || errorCode(t, later) != "budget_state_unavailable" || g.managed.calls() != 1 {
		t.Fatalf("overshoot failed to block: %d %s calls=%d", later.Code, later.Body, g.managed.calls())
	}
}

func TestCapMirrorNeverDispatches(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	var lookedUp atomic.Int64
	g.srv.handlers.mirror.Store(routing.NewMirror(config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{{SourceModel: "*", TargetProvider: "synthetic-mirror", SampleRate: 1}}}, func(string) (routing.MirrorProvider, bool) { lookedUp.Add(1); return nil, false }))
	if w := g.chat(t, chatBody("openai/gpt-6-sol"), nil); w.Code != http.StatusOK {
		t.Fatalf("strict control: %d %s", w.Code, w.Body)
	}
	if lookedUp.Load() != 0 {
		t.Fatal("capped request entered secondary dispatch")
	}
	cfg := g.tenants.Get("org-m")
	cfg.Budgets.AdmissionCapsEnabled = false
	g.tenants.Set("org-m", cfg)
	if w := g.chat(t, chatBody("openai/gpt-6-sol"), nil); w.Code != http.StatusOK {
		t.Fatalf("off control: %d %s", w.Code, w.Body)
	}
	if lookedUp.Load() != 1 {
		t.Fatal("uncapped mirror positive control unreachable")
	}
}

func TestCapLateRewriteVoidsWalletAndBudget(t *testing.T) {
	rewrite := &capLateRewrite{on: true}
	g, client, prefix := capComposedGateway(t, rewrite)
	w := g.chat(t, chatBody("openai/gpt-6-sol"), nil)
	if w.Code != http.StatusPaymentRequired || errorCode(t, w) != "call_cap_unenforceable" || g.managed.calls() != 0 {
		t.Fatalf("rewritten request escaped: %d %s calls=%d", w.Code, w.Body, g.managed.calls())
	}
	account := g.account(t, "org-m")
	records := g.outbox("org-m")
	if account.HeldMicros != 0 || account.SpentMicros != 0 || len(records) != 1 || records[0]["kind"] != "void" || records[0]["billed_micros"] != "0" {
		t.Fatalf("refusal must preserve one zero-debit wallet void: %+v outbox=%v", account, records)
	}
	state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
	if got := client.Redis().HGet(context.Background(), state, "held:agent:worker").Val(); got != "0" {
		t.Fatalf("cap hold leaked: %s", got)
	}
	rewrite.on = false
	if w := g.chat(t, chatBody("openai/gpt-6-sol"), nil); w.Code != http.StatusOK {
		t.Fatalf("valid next request blocked: %d %s", w.Code, w.Body)
	}
}
